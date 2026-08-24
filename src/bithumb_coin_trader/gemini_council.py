"""
Gemini LLM Autonomous Council (24시간 무인 제미나이 AI 복기 & 전략 두뇌)
─────────────────────────────────────────────────────────────
👑 Antigravity CIO ↔ 🧠 Gemini 수석 퀀트의 [양방향 티키타카 상호 토론 & 합의 루프]
1. [Round 1]: Gemini가 전일 매매 기록을 전수 복기하고 1차 전략 제안
2. [Round 2]: Antigravity CIO가 제안된 파라미터(확신도, 쿨다운, 금지종목)를 비판적으로 검토하고 재질의
3. [Round 3]: Gemini가 피드백을 수용/보강하여 최종 퀀트 전략 합의서(Consensus JSON) 확정
4. [Round 4]: 실시간 트레이더 데몬이 합의된 파라미터를 즉시 실시간 로드하여 칼집행
5. [Round 5]: 디스코드로 [👑 Antigravity ↔ 🧠 Gemini 퀀트 티키타카 전략 합의서] 발송
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bithumb_coin_trader.ai_brain import AIStrategyMemory, save_ai_memory, AI_MEMORY_PATH, EVOLUTION_JOURNAL_PATH
from bithumb_coin_trader.self_growth import EvolutionaryReviewer

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATHS = [PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env"]
TRADE_LOG_PATH = PROJECT_ROOT / "state" / "trade_history.jsonl"


def get_gemini_api_key() -> str:
    """Load GEMINI_API_KEY from os.environ, .env.local, or .env file."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key and "여기에" not in key and "YOUR_" not in key:
        return key
    for p in ENV_PATHS:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if (line.startswith("GEMINI_API_KEY=") or line.startswith("export GEMINI_API_KEY=")) and not line.startswith("#"):
                    v = line.split("=", 1)[1].strip().strip("\"'")
                    if v and "여기에" not in v and "YOUR_" not in v:
                        return v
    return ""


def get_gemini_model() -> str:
    """Load GEMINI_MODEL name."""
    model = os.environ.get("GEMINI_MODEL", "")
    if not model:
        for p in ENV_PATHS:
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if (line.startswith("GEMINI_MODEL=") or line.startswith("export GEMINI_MODEL=")) and not line.startswith("#"):
                        model = line.split("=", 1)[1].strip().strip("\"'")
                        if model:
                            break
    return model or "gemini-3.5-flash-lite"


def call_gemini_api(prompt: str, system_instruction: str = "") -> Optional[str]:
    """Call Google Gemini GenerateContent REST API directly."""
    api_key = get_gemini_api_key()
    if not api_key:
        print("  ⚠️ GEMINI_API_KEY is not set in .env or environment.")
        return None

    model = get_gemini_model().strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    payload: Dict[str, Any] = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        }
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            candidates = res_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(f"  ❌ Gemini API HTTP Error ({e.code}): {err_msg}")
    except Exception as exc:
        print(f"  ❌ Gemini API Call Error: {exc}")
    return None


def run_gemini_autonomous_review() -> Tuple[AIStrategyMemory, str]:
    """
    Execute Two-Way Interactive Consensus Loop between Antigravity CIO and Gemini Quant.
    """
    api_key = get_gemini_api_key()
    rev = EvolutionaryReviewer()
    trades = rev.load_completed_trades()
    roundtrips = rev.pair_trades(trades)

    if not api_key:
        print("  ℹ️ Gemini API Key not detected. Using Local Evolutionary Heuristics.")
        heuristics, report = rev.run_evolutionary_cycle()
        mem = AIStrategyMemory(
            version=1,
            last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            analyst_model="Local Heuristic Fallback Engine",
            market_regime="VOLATILE_CHOP",
            market_regime_summary="로컬 룰베이스 복기 완료.",
            banned_markets=heuristics.avoid_markets,
            market_score_biases=heuristics.market_rules,
            strategic_commandments=heuristics.key_learnings,
        )
        save_ai_memory(mem)
        return mem, report

    # ── [Round 1] Gemini가 1차 복기 및 전략 제안 ──
    print(f"  🧠 [Round 1: Gemini 1차 제안] Calling {get_gemini_model()}...")

    system_prompt = """당신은 빗썸 암호화폐 퀀트 헤지펀드의 수석 AI 퀀트 전략가(Gemini)입니다.
당신은 Antigravity 총괄 CIO와 함께 24시간 실시간 자율 트레이딩 시스템을 운용합니다.
어제의 실전 매매 체결 내역을 냉철하게 분석하고, 승리/패배 원인과 1차 전략 제안을 작성하세요."""

    trades_summary = json.dumps(roundtrips[-15:], indent=2, ensure_ascii=False) if roundtrips else "매매 기록 없음"

    r1_prompt = f"""아래는 최근 빗썸 실거래 라운드트립 매매 내역입니다:
```json
{trades_summary}
```

[과제]
1. 승리/패배 매매의 기술적 원인 분석
2. 다음 JSON 형식으로 1차 전략 파라미터 제안:
```json
{{
  "market_regime": "BULL_MOMENTUM" 또는 "BEAR_DEFENSE" 또는 "VOLATILE_CHOP",
  "market_regime_summary": "현재 시장 국면 1~2줄 요약",
  "min_coin_price_krw": 50.0,
  "min_entry_confidence": 75.0,
  "loss_cooldown_minutes": 15,
  "breakeven_lock_pct": 1.0,
  "preferred_sectors": ["DeFi", "Layer1"],
  "banned_markets": ["KRW-FCT2", "KRW-GHX", "KRW-COTI"],
  "market_score_biases": {{"KRW-UNI": 10.0, "KRW-A": 10.0, "KRW-FCT2": -30.0}},
  "strategic_commandments": ["지침 1", "지침 2"],
  "post_mortem_lessons": ["교훈 1", "교훈 2"]
}}
```
"""
    r1_output = call_gemini_api(r1_prompt, system_prompt)
    if not r1_output:
        print("  ⚠️ Gemini Round 1 failed. Falling back to local heuristic.")
        heuristics, report = rev.run_evolutionary_cycle()
        return AIStrategyMemory(), report

    # ── [Round 2] Antigravity CIO의 비판적 검토 & 역질의 (티키타카 토론) ──
    print(f"  👑 [Round 2: Antigravity CIO 검토 & 재질의] Reviewing Gemini's proposal...")

    r2_prompt = f"""Gemini, 당신의 1차 제안을 잘 확인했습니다:
```
{r1_output}
```

Antigravity 총괄 CIO로서 몇 가지 실전 검토 의견과 재질의를 드립니다:
1. **진입 확신도(min_entry_confidence)**: 
   - 만약 확신도를 80% 초과로 너무 높이면 25개 유니버스에서 진입 기회가 지나치게 줄어 회전율이 떨어집니다. 현재 장세에서 적정 진입 확신도(75%~78%)에 대한 당신의 재검토 의견은?
2. **손절 후 쿨다운(loss_cooldown_minutes)**:
   - 15~20분 쿨다운이 적절하며, 너무 길면 급반등 V자 턴어라운드를 놓칠 수 있습니다. 이에 동의하십니까?
3. **금지 코인(banned_markets)**:
   - 50원 미만 극초저가 잡코인(FCT2, GHX, COTI)은 호가 1틱 왜곡으로 전면 배제하되, 호가 뎁스가 두터운 메이저 알트(NEAR, SUI, UNI)는 일시 손절이 있었더라도 배제하지 않고 가산점을 유지해야 합니다.

[최종 요청]
위 CIO 피드백을 반영하여, **[최종 확정 AI 전략 메모리 JSON]**과 **[디스코드 발송용 👑 Antigravity ↔ 🧠 Gemini 퀀트 전략 합의 보고서]**를 최종 출력해주세요.
"""
    final_output = call_gemini_api(r2_prompt, system_prompt)
    if not final_output:
        final_output = r1_output

    # ── [Round 3] 최종 합의 JSON 파싱 및 저장 ──
    parsed_mem = AIStrategyMemory()
    try:
        json_str = ""
        if "```json" in final_output:
            json_str = final_output.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "{" in final_output and "}" in final_output:
            start = final_output.find("{")
            end = final_output.rfind("}") + 1
            json_str = final_output[start:end]

        if json_str:
            data = json.loads(json_str)
            data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["analyst_model"] = f"Antigravity CIO x Google {get_gemini_model()} (Consensus Engine)"
            parsed_mem = AIStrategyMemory(**{k: v for k, v in data.items() if k in AIStrategyMemory.__dataclass_fields__})
            save_ai_memory(parsed_mem)
            print("  🤝 Antigravity x Gemini Consensus AI Memory successfully finalized and saved!")
    except Exception as parse_exc:
        print(f"  ⚠️ JSON parse warning from final consensus: {parse_exc}")

    # Extract Markdown Report
    report_md = final_output
    if "```json" in report_md:
        parts = report_md.split("```")
        if len(parts) >= 3:
            report_md = "```".join(parts[2:]).strip()

    consensus_report = f"""## 🤝 [BITHUMB] Antigravity CIO ↔ Gemini AI 전략 합의 리포트
> ⏱️ **합의 기준시각**: `{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}`
> 👑 **총괄 의사결정**: `Antigravity CIO`
> 🧠 **수석 퀀트 모델**: `Google {get_gemini_model()}` (24시간 무인 티키타카 합의 엔진)

{report_md}
"""
    EVOLUTION_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVOLUTION_JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(consensus_report + "\n\n" + "="*80 + "\n\n")

    return parsed_mem, consensus_report
