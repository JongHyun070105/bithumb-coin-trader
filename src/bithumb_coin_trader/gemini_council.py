"""
Gemini LLM Autonomous Council (24시간 무인 제미나이 AI 복기 & 전략 두뇌)
─────────────────────────────────────────────────────────────
사용자가 없어도 24시간 내내 매일 밤 자정(00:00 KST) 또는 일자 변경 시:
1. Google Gemini Flash Lite LLM API를 직접 호출
2. 당일 전체 매매 기록과 빗썸 실시간 캔들/오더북/거래대금을 프롬프트로 전송
3. LLM이 심층 추론(Reasoning)하여 작성한 ai_strategy_memory.json 자동 갱신
4. LLM이 직접 쓴 자가성장 복기 보고서를 디스코드로 자동 발송
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
    Execute 100% LLM-driven post-mortem review and strategy evolution.
    If Gemini API key is available, uses actual Gemini LLM.
    Otherwise falls back to the statistical engine gracefully.
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

    # ── Real Gemini LLM Reasoning Cycle ──
    print(f"  🧠 [Gemini Autonomous Council] Calling {get_gemini_model()} for deep reasoning...")

    system_prompt = """당신은 빗썸 암호화폐 퀀트 헤지펀드의 최고투자책임자(CIO)이자 수석 AI 퀀트 전략가입니다.
당신의 임무는 어제의 실제 매매 체결 기록(라운드트립)을 전수 분석하여:
1. 승리한 매매의 원인과 패배(손절)한 매매의 기술적/구조적 원인을 냉철하게 규명하고
2. 내일 24시간 동안 실시간 트레이더가 준수해야 할 정량적 전략 파라미터(JSON)와
3. 디스코드로 발행할 정밀 사후 복기 보고서(Markdown)를 작성하는 것입니다."""

    trades_summary = json.dumps(roundtrips[-15:], indent=2, ensure_ascii=False) if roundtrips else "매매 기록 없음"

    user_prompt = f"""아래는 최근 빗썸 실거래 라운드트립 매매 내역입니다:
```json
{trades_summary}
```

위 실전 데이터를 바탕으로 두 가지 산출물을 작성하세요:

[산출물 1] 다음 정확한 JSON 형식으로 실시간 트레이더에게 주입할 AI 전략 메모리를 작성하세요:
```json
{{
  "market_regime": "BULL_MOMENTUM" 또는 "BEAR_DEFENSE" 또는 "VOLATILE_CHOP",
  "market_regime_summary": "현재 시장 국면 1~2줄 요약",
  "min_coin_price_krw": 50.0,
  "min_entry_confidence": 75.0,
  "loss_cooldown_minutes": 15,
  "breakeven_lock_pct": 1.0,
  "preferred_sectors": ["선호 섹터 1", "선호 섹터 2"],
  "banned_markets": ["KRW-FCT2", "KRW-GHX", "KRW-COTI"],
  "market_score_biases": {{
    "KRW-LINK": 5.0,
    "KRW-ETC": 5.0,
    "KRW-COTI": -20.0
  }},
  "strategic_commandments": [
    "지침 1",
    "지침 2",
    "지침 3",
    "지침 4"
  ],
  "post_mortem_lessons": [
    "복기 교훈 1",
    "복기 교훈 2"
  ]
}}
```

[산출물 2] 디스코드에 발송할 [🧬 Gemini AI 일일 자가성장 & 사후 복기 리포트] 마크다운 본문을 작성하세요.
반드시 승리 요인과 패배 요인을 명확히 짚어주세요.
"""

    llm_output = call_gemini_api(user_prompt, system_prompt)
    if not llm_output:
        print("  ⚠️ Gemini LLM call failed. Falling back to local heuristic.")
        heuristics, report = rev.run_evolutionary_cycle()
        return AIStrategyMemory(), report

    # Parse JSON from LLM output
    parsed_mem = AIStrategyMemory()
    try:
        json_str = ""
        if "```json" in llm_output:
            json_str = llm_output.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "{" in llm_output and "}" in llm_output:
            start = llm_output.find("{")
            end = llm_output.rfind("}") + 1
            json_str = llm_output[start:end]

        if json_str:
            data = json.loads(json_str)
            data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["analyst_model"] = f"Google {get_gemini_model()}"
            parsed_mem = AIStrategyMemory(**{k: v for k, v in data.items() if k in AIStrategyMemory.__dataclass_fields__})
            save_ai_memory(parsed_mem)
            print("  ✅ Gemini AI Strategy Memory successfully parsed and saved!")
    except Exception as parse_exc:
        print(f"  ⚠️ JSON parse warning from Gemini response: {parse_exc}")

    # Extract Markdown Report
    report_md = llm_output
    if "```json" in report_md:
        parts = report_md.split("```")
        if len(parts) >= 3:
            report_md = "```".join(parts[2:]).strip()

    full_report = f"""## 🧬 [BITHUMB] Gemini AI 일일 자가 성장 & 사후 복기 리포트
> ⏱️ **복기 기준시각**: `{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}`
> 🧠 **AI 분석 모델**: `{get_gemini_model()}` (24시간 무인 자가 진화)

{report_md}
"""
    # Append to EVOLUTION_JOURNAL.md
    EVOLUTION_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVOLUTION_JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(full_report + "\n\n" + "="*80 + "\n\n")

    return parsed_mem, full_report
