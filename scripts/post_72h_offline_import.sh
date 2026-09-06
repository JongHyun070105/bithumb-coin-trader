#!/usr/bin/env bash
# =============================================================================
# 72H Post-Soak Offline Import Pipeline (P16)
# =============================================================================
# Executes the authoritative 6-stage post-soak offline import runbook sequence:
# 1. Authoritative Deep DQ Audit (scripts/audit_72h_soak.py)
# 2. Build Sealed Epoch Root Manifest (scripts/build_epoch_manifest.py)
# 3. Cryptographic DQ Qualification Artifact (research_cli dq-qualify)
# 4. Canonicalize Orderbook Stream (research_cli transform-canonical)
# 5. Canonicalize Trade Stream (research_cli transform-canonical)
# 6. Partition Dataset with Embargo Windows (research_cli partition-dataset)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Defaults
EPOCH_DIR="${EPOCH_DIR:-${1:-$REPO_ROOT/data/exported_soak_72h}}"
REPORTS_DIR="${REPORTS_DIR:-$REPO_ROOT/reports}"
EVIDENCE_DIR="${EVIDENCE_DIR:-$REPO_ROOT/evidence/research}"
CANONICAL_DIR="${CANONICAL_DIR:-$REPO_ROOT/data/canonical_72h}"
DATASET_DIR="${DATASET_DIR:-$REPO_ROOT/data/datasets/krw_btc_72h_v1}"
EXCHANGE="${EXCHANGE:-bithumb}"
MARKET="${MARKET:-KRW-BTC}"
PYTHON="${PYTHON:-python3}"
CONTRACT="${CONTRACT:-}"
RUNTIME_SEAL="${RUNTIME_SEAL:-}"
LAUNCH_PROVENANCE="${LAUNCH_PROVENANCE:-}"
ACTUAL_START_EVIDENCE="${ACTUAL_START_EVIDENCE:-}"
SYNTHETIC_ACTUAL_START="${SYNTHETIC_ACTUAL_START:-}"

# Parse optional flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --epoch-dir)
            EPOCH_DIR="$2"
            shift 2
            ;;
        --reports-dir)
            REPORTS_DIR="$2"
            shift 2
            ;;
        --evidence-dir)
            EVIDENCE_DIR="$2"
            shift 2
            ;;
        --canonical-dir)
            CANONICAL_DIR="$2"
            shift 2
            ;;
        --dataset-dir)
            DATASET_DIR="$2"
            shift 2
            ;;
        --exchange)
            EXCHANGE="$2"
            shift 2
            ;;
        --market)
            MARKET="$2"
            shift 2
            ;;
        --contract)
            CONTRACT="$2"
            shift 2
            ;;
        --runtime-seal)
            RUNTIME_SEAL="$2"
            shift 2
            ;;
        --launch-provenance)
            LAUNCH_PROVENANCE="$2"
            shift 2
            ;;
        --actual-start-evidence)
            ACTUAL_START_EVIDENCE="$2"
            shift 2
            ;;
        --synthetic-actual-start)
            SYNTHETIC_ACTUAL_START="$2"
            shift 2
            ;;
        --python)
            PYTHON="$2"
            shift 2
            ;;
        *)
            # positional argument fallback
            EPOCH_DIR="$1"
            shift 1
            ;;
    esac
done

export PYTHONPATH="$REPO_ROOT/src:$REPO_ROOT:${PYTHONPATH:-}"

echo "==============================================================================="
echo "72H OFFLINE IMPORT PIPELINE START"
echo "  Epoch Directory:     $EPOCH_DIR"
echo "  Reports Directory:   $REPORTS_DIR"
echo "  Evidence Directory:  $EVIDENCE_DIR"
echo "  Canonical Directory: $CANONICAL_DIR"
echo "  Dataset Directory:   $DATASET_DIR"
echo "  Exchange / Market:   $EXCHANGE / $MARKET"
echo "==============================================================================="

if [ ! -d "$EPOCH_DIR" ]; then
    echo "ERROR: Epoch directory does not exist: $EPOCH_DIR" >&2
    exit 2
fi

mkdir -p "$REPORTS_DIR"
mkdir -p "$EVIDENCE_DIR"
mkdir -p "$CANONICAL_DIR"

COMPOSED_CONTRACT="$EPOCH_DIR/contracts/epoch_contract.json"
EPOCH_MANIFEST="$EPOCH_DIR/manifests/epoch_manifest.json"
DEEP_DQ_REPORT="$REPORTS_DIR/deep_dq_audit_72h.json"
DEEP_DQ_MD="$REPORTS_DIR/deep_dq_audit_72h.md"
DQ_QUALIFICATION="$EVIDENCE_DIR/dq_qualification_72h.json"
CANONICAL_MANIFEST="$CANONICAL_DIR/canonical_manifest.json"

# -----------------------------------------------------------------------------
# Stage 1: Verify or Compose Run Contract (P1, P17)
# -----------------------------------------------------------------------------
echo "[Stage 1/6] COMPOSE/VERIFY CONTRACT..."
if [ -z "$CONTRACT" ]; then
    if [ -f "$EPOCH_DIR/contracts/epoch_contract.json" ]; then
        CONTRACT="$EPOCH_DIR/contracts/epoch_contract.json"
    elif [ -f "$EPOCH_DIR/epoch_contract.json" ]; then
        CONTRACT="$EPOCH_DIR/epoch_contract.json"
    fi
fi

if [ -z "$CONTRACT" ]; then
    # Auto-detect seals if not explicitly provided
    if [ -z "$RUNTIME_SEAL" ]; then
        for c in "$EPOCH_DIR/contracts/runtime_seal.json" "$EPOCH_DIR/runtime_seal.json" "$EPOCH_DIR/contracts/runtime.json" "$EPOCH_DIR/runtime.json"; do
            if [ -f "$c" ]; then RUNTIME_SEAL="$c"; break; fi
        done
    fi
    if [ -z "$LAUNCH_PROVENANCE" ]; then
        for c in "$EPOCH_DIR/contracts/launch-provenance.json" "$EPOCH_DIR/launch-provenance.json"; do
            if [ -f "$c" ]; then LAUNCH_PROVENANCE="$c"; break; fi
        done
    fi
    if [ -z "$ACTUAL_START_EVIDENCE" ]; then
        for c in "$EPOCH_DIR/contracts/actual_start.evidence.json" "$EPOCH_DIR/actual_start.evidence.json" "$EPOCH_DIR/actual_start_evidence.json"; do
            if [ -f "$c" ]; then ACTUAL_START_EVIDENCE="$c"; break; fi
        done
    fi

    mkdir -p "$EPOCH_DIR/contracts"
    COMP_ARGS=(
        "$REPO_ROOT/scripts/compose_epoch_contract.py"
        "--output" "$COMPOSED_CONTRACT"
    )
    if [ -n "$RUNTIME_SEAL" ]; then
        COMP_ARGS+=("--runtime-seal" "$RUNTIME_SEAL")
    fi
    if [ -n "$LAUNCH_PROVENANCE" ]; then
        COMP_ARGS+=("--launch-provenance" "$LAUNCH_PROVENANCE")
    fi
    if [ -n "$ACTUAL_START_EVIDENCE" ]; then
        COMP_ARGS+=("--actual-start-evidence" "$ACTUAL_START_EVIDENCE")
    fi
    if [ -n "$SYNTHETIC_ACTUAL_START" ]; then
        COMP_ARGS+=("--synthetic-actual-start" "$SYNTHETIC_ACTUAL_START")
    fi
    "$PYTHON" "${COMP_ARGS[@]}"
    CONTRACT="$COMPOSED_CONTRACT"
    echo "✓ Stage 1 Complete: Contract composed and verified at $CONTRACT."
else
    echo "✓ Stage 1 Complete: Using provided contract at $CONTRACT."
fi

# -----------------------------------------------------------------------------
# Stage 2: Build Sealed Epoch Root Manifest (P1, P3, P11, P12, P13)
# -----------------------------------------------------------------------------
echo "[Stage 2/6] BUILD ROOT..."
MANIFEST_ARGS=(
    "$REPO_ROOT/scripts/build_epoch_manifest.py"
    "--epoch-dir" "$EPOCH_DIR"
    "--output" "$EPOCH_MANIFEST"
    "--contract" "$CONTRACT"
    "--strict"
)
"$PYTHON" "${MANIFEST_ARGS[@]}"
echo "✓ Stage 2 Complete: Epoch Root Manifest sealed."

# -----------------------------------------------------------------------------
# Stage 3: Authoritative Deep DQ Audit Against Root (P1, P1.1, P2)
# -----------------------------------------------------------------------------
echo "[Stage 3/6] DEEP AUDIT..."
AUDIT_ARGS=(
    "$REPO_ROOT/scripts/audit_72h_soak.py"
    "--epoch-dir" "$EPOCH_DIR"
    "--epoch-manifest" "$EPOCH_MANIFEST"
    "--contract" "$CONTRACT"
    "--out-json" "$DEEP_DQ_REPORT"
    "--out-md" "$DEEP_DQ_MD"
)
"$PYTHON" "${AUDIT_ARGS[@]}"
echo "✓ Stage 3 Complete: Deep DQ Audit verified against epoch root."

# -----------------------------------------------------------------------------
# Stage 4: Cryptographic DQ Qualification (P4, P4.1)
# -----------------------------------------------------------------------------
echo "[Stage 4/6] QUALIFY..."
"$PYTHON" -m bithumb_coin_trader.research_cli dq-qualify \
    --audit-report "$DEEP_DQ_REPORT" \
    --epoch-manifest "$EPOCH_MANIFEST" \
    --out "$DQ_QUALIFICATION" \
    --strict
echo "✓ Stage 4 Complete: DQ Qualification artifact bound."

# -----------------------------------------------------------------------------
# Stage 5: Canonicalize Streams (P8, P14)
# -----------------------------------------------------------------------------
echo "[Stage 5/6] CANONICALIZE..."
echo "  Transforming Orderbook stream..."
"$PYTHON" -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir "$CANONICAL_DIR" \
    --exchange "$EXCHANGE" \
    --stream "orderbook" \
    --schema-version "2.1.0" \
    --epoch-manifest "$EPOCH_MANIFEST" \
    --dq-qualification "$DQ_QUALIFICATION"

echo "  Transforming Trade stream..."
"$PYTHON" -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir "$CANONICAL_DIR" \
    --exchange "$EXCHANGE" \
    --stream "trade" \
    --schema-version "2.1.0" \
    --epoch-manifest "$EPOCH_MANIFEST" \
    --dq-qualification "$DQ_QUALIFICATION"
echo "✓ Stage 5 Complete: Canonical streams transformed and bound."

# -----------------------------------------------------------------------------
# Stage 6: Partition Dataset with Embargo Windows (P5, P6, P7, P15, P16)
# -----------------------------------------------------------------------------
echo "[Stage 6/6] PARTITION..."
"$PYTHON" -m bithumb_coin_trader.research_cli partition-dataset \
    --canonical-manifest "$CANONICAL_MANIFEST" \
    --exchange "$EXCHANGE" \
    --market "$MARKET" \
    --stream "orderbook" \
    --output-dir "$DATASET_DIR" \
    --dq-report "$DQ_QUALIFICATION" \
    --epoch-manifest "$EPOCH_MANIFEST" \
    --deep-audit-report "$DEEP_DQ_REPORT" \
    --train-frac 0.60 \
    --val-frac 0.20 \
    --purge-window-ms 900000 \
    --clock receive_wall_clock

echo "==============================================================================="
echo "✓ 72H OFFLINE IMPORT PIPELINE COMPLETED SUCCESSFULLY"
echo "  Dataset sealed at: $DATASET_DIR"
echo "==============================================================================="
