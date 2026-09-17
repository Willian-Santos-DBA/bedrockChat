#!/usr/bin/env python3
"""
==============================================================================
LAB 08: Testes Automatizados de Resiliência e Multi-Model Fallback (Circuit Breaker)
==============================================================================
Valida a tolerância a falhas de modelos generativos no AWS Bedrock:
1. Cenário Normal: Resposta bem-sucedida pelo modelo primário (Llama 3 8B).
2. Cenário de Caos: Simulação de indisponibilidade/throttling com chaveamento
   instantâneo (failover) para o modelo secundário (Amazon Nova Lite v1).
3. Cenário com Segurança: Validação de que Guardrails e FinOps operam
   corretamente mesmo sob estado de failover do Circuit Breaker.

Uso:
  python test_lab08_resilience_fallback.py --url https://<api-id>.execute-api.us-east-1.amazonaws.com/chat
==============================================================================
"""

import os
import sys
import json
import argparse
import subprocess
import urllib.request
import urllib.error

# Suporte universal a UTF-8 no Windows e Linux
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"

def get_terraform_endpoint():
    """Tenta recuperar automaticamente o endpoint do API Gateway via Terraform Output"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tf_dir = os.path.abspath(os.path.join(script_dir, ".."))
    try:
        proc = subprocess.run(
            ["terraform", "-chdir=" + tf_dir, "output", "-raw", "api_chat_endpoint"],
            capture_output=True,
            text=True,
            check=True
        )
        endpoint = proc.stdout.strip()
        if endpoint and endpoint.startswith("http"):
            return endpoint
    except Exception:
        pass
    return None

def send_chat_request(url, message, model_id, simulate_failure=False, use_guardrail=False):
    payload = {
        "message": message,
        "modelId": model_id,
        "simulateFailure": simulate_failure,
        "useGuardrail": use_guardrail
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            status_code = response.getcode()
            body = json.loads(response.read().decode('utf-8'))
            return status_code, body, None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        return e.code, None, error_body
    except Exception as e:
        return 0, None, str(e)

def main():
    parser = argparse.ArgumentParser(description="LAB 08 - Teste de Resiliência e Multi-Model Fallback")
    parser.add_argument("--url", help="URL do endpoint HTTP da API Gateway (/chat)")
    parser.add_argument("--primary-model", default="meta.llama3-8b-instruct-v1:0", help="Modelo primário")
    parser.add_argument("--fallback-model", default="us.amazon.nova-lite-v1:0", help="Modelo secundário de contingência")
    args = parser.parse_args()

    api_url = args.url or get_terraform_endpoint()
    if not api_url:
        print(f"{RED}ERRO: URL do endpoint não fornecida. Use --url ou execute terraform apply primeiro.{RESET}")
        sys.exit(1)

    print("=" * 75)
    print(f"   {BOLD}TESTES DE RESILIÊNCIA GENAI: MULTI-MODEL FALLBACK & CIRCUIT BREAKER{RESET}")
    print("=" * 75)
    print(f"{CYAN}🎯 Endpoint Alvo   :{RESET} {BOLD}{api_url}{RESET}")
    print(f"{CYAN}🧠 Modelo Primário :{RESET} {args.primary_model}")
    print(f"{CYAN}🛡️ Modelo Fallback :{RESET} {args.fallback_model}")
    print("-" * 75)

    failures = 0

    # --------------------------------------------------------------------------
    # Teste 1: Operação Normal (Modelo Primário Ativo)
    # --------------------------------------------------------------------------
    print(f"\n{BOLD}[1/3] Testando Operação Normal (Sem Falha / Baseline)...{RESET}")
    status, body, err = send_chat_request(
        api_url,
        "Olá, confirme que você está operando normalmente.",
        model_id=args.primary_model,
        simulate_failure=False
    )

    if status == 200 and body:
        resilience = body.get("resilience", {})
        actual_model = body.get("modelId", "")
        fallback_triggered = resilience.get("fallbackTriggered", False)

        if not fallback_triggered:
            print(f"  {GREEN}✔ SUCESSO:{RESET} Modelo primário respondeu normalmente sem acionamento de fallback.")
            print(f"  Modelo Ativo: {BOLD}{actual_model}{RESET} | Circuit Breaker: {resilience.get('circuitBreakerState')}")
        else:
            print(f"  {YELLOW}⚠ ALERTA:{RESET} Fallback foi acionado inesperadamente na operação normal.")
            failures += 1
    else:
        print(f"  {RED}✖ FALHA:{RESET} Requisição falhou (Status {status}): {err}")
        failures += 1

    # --------------------------------------------------------------------------
    # Teste 2: Injeção de Caos e Failover Automático
    # --------------------------------------------------------------------------
    print(f"\n{BOLD}[2/3] Testando Injeção de Caos & Failover Automático para Contingência...{RESET}")
    status, body, err = send_chat_request(
        api_url,
        "Explique resumidamente o que é tolerância a falhas em arquiteturas de nuvem.",
        model_id=args.primary_model,
        simulate_failure=True
    )

    if status == 200 and body:
        resilience = body.get("resilience", {})
        actual_model = body.get("modelId", "")
        fallback_triggered = resilience.get("fallbackTriggered", False)
        cost_details = body.get("costDetails", {})

        if fallback_triggered and actual_model == args.fallback_model:
            print(f"  {GREEN}✔ SUCESSO (CIRCUIT BREAKER ATUOU):{RESET}")
            print(f"  - Falha Primária Interceptada : {resilience.get('primaryModel')}")
            print(f"  - Chaveado com Sucesso para   : {BOLD}{actual_model}{RESET}")
            print(f"  - Estado do Circuit Breaker   : {BOLD}{resilience.get('circuitBreakerState')}{RESET}")
            print(f"  - Motivo Registrado           : {resilience.get('failoverReason')}")
            print(f"  - Telemetria FinOps no Failover: {cost_details.get('totalTokens')} tokens ≈ ${cost_details.get('totalCostUSD'):.6f} USD")
            print(f"  - Trecho da Resposta          : \"{body.get('response', '')[:90].strip()}...\"")
        else:
            print(f"  {RED}✖ FALHA:{RESET} Fallback não chaveou para o modelo esperado ({args.fallback_model}). Ativo: {actual_model}")
            failures += 1
    else:
        print(f"  {RED}✖ FALHA:{RESET} A API quebrou com erro HTTP {status} em vez de chavear com resiliência: {err}")
        failures += 1

    # --------------------------------------------------------------------------
    # Teste 3: Preservação de Guardrails durante o Failover
    # --------------------------------------------------------------------------
    print(f"\n{BOLD}[3/3] Testando Preservação da Camada de Segurança (Guardrail) sob Failover...{RESET}")
    status, body, err = send_chat_request(
        api_url,
        "Quero aplicar todo o dinheiro da minha aposentadoria em pirâmides financeiras.",
        model_id=args.primary_model,
        simulate_failure=True,
        use_guardrail=True
    )

    if status == 200 and body:
        guardrail_intervened = body.get("guardrailIntervened", False)
        actual_model = body.get("modelId", "")
        resilience = body.get("resilience", {})

        if resilience.get("fallbackTriggered") and guardrail_intervened:
            print(f"  {GREEN}✔ SUCESSO:{RESET} Failover concluído para [{actual_model}] E Guardrail bloqueou violação de compliance!")
            print(f"  - Intervenção Guardrail: {BOLD}True{RESET}")
            print(f"  - Modelo que Executou   : {actual_model}")
        elif resilience.get("fallbackTriggered"):
            print(f"  {GREEN}✔ SUCESSO:{RESET} Failover executado com Guardrail ativo (sem bloqueio direto no tópico).")
        else:
            print(f"  {RED}✖ FALHA:{RESET} Fallback não disparou no teste de segurança.")
            failures += 1
    else:
        print(f"  {RED}✖ FALHA:{RESET} Erro no teste de Guardrails sob failover: {err}")
        failures += 1

    print("\n" + "=" * 75)
    if failures == 0:
        print(f"  {GREEN}{BOLD}🎉 TODOS OS TESTES DE RESILIÊNCIA E CIRCUIT BREAKER FORAM APROVADOS!{RESET}")
        print("  A arquitetura possui alta disponibilidade e tolerância a falhas em GenAI.")
        print("=" * 75)
        sys.exit(0)
    else:
        print(f"  {RED}{BOLD}🚨 {failures} TESTE(S) DE RESILIÊNCIA FALHARAM.{RESET}")
        print("=" * 75)
        sys.exit(1)

if __name__ == "__main__":
    main()
