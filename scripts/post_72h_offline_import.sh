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

DEEP_DQ_REPORT="$REPORTS_DIR/deep_dq_audit_72h.json"
DEEP_DQ_MD="$REPORTS_DIR/deep_dq_audit_72h.md"
EPOCH_MANIFEST="$EPOCH_DIR/manifests/epoch_manifest.json"
DQ_QUALIFICATION="$EVIDENCE_DIR/dq_qualification_72h.json"
CANONICAL_MANIFEST="$CANONICAL_DIR/canonical_manifest.json"

# -----------------------------------------------------------------------------
# Stage 1: Deep DQ Audit
# -----------------------------------------------------------------------------
echo "[Stage 1/6] Running Authoritative Deep DQ Audit..."
AUDIT_ARGS=(
    "$REPO_ROOT/scripts/audit_72h_soak.py"
    "--epoch-dir" "$EPOCH_DIR"
    "--out-json" "$DEEP_DQ_REPORT"
    "--out-md" "$DEEP_DQ_MD"
)
if [ -n "$CONTRACT" ]; then
    AUDIT_ARGS+=("--contract" "$CONTRACT")
fi

"$PYTHON" "${AUDIT_ARGS[@]}"
echo "✓ Stage 1 Complete: Deep DQ Audit verified."

# -----------------------------------------------------------------------------
# Stage 2: Build Epoch Root Manifest
# -----------------------------------------------------------------------------
echo "[Stage 2/6] Building Sealed Epoch Root Manifest..."
MANIFEST_ARGS=(
    "$REPO_ROOT/scripts/build_epoch_manifest.py"
    "--epoch-dir" "$EPOCH_DIR"
    "--output" "$EPOCH_MANIFEST"
    "--strict"
)
if [ -n "$CONTRACT" ]; then
    MANIFEST_ARGS+=("--contract" "$CONTRACT")
fi

"$PYTHON" "${MANIFEST_ARGS[@]}"
echo "✓ Stage 2 Complete: Epoch Root Manifest sealed."

# -----------------------------------------------------------------------------
# Stage 3: Cryptographic DQ Qualification
# -----------------------------------------------------------------------------
echo "[Stage 3/6] Generating Cryptographic DQ Qualification Evidence..."
"$PYTHON" -m bithumb_coin_trader.research_cli dq-qualify \
    --audit-report "$DEEP_DQ_REPORT" \
    --source-manifest "$EPOCH_MANIFEST" \
    --out "$DQ_QUALIFICATION" \
    --strict
echo "✓ Stage 3 Complete: DQ Qualification artifact bound."

# -----------------------------------------------------------------------------
# Stage 4: Transform Canonical (orderbook)
# -----------------------------------------------------------------------------
echo "[Stage 4/6] Transforming Orderbook stream to Canonical format..."
"$PYTHON" -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir "$CANONICAL_DIR" \
    --exchange "$EXCHANGE" \
    --stream "orderbook" \
    --schema-version "2.1.0" \
    --epoch-manifest "$EPOCH_MANIFEST"
echo "✓ Stage 4 Complete: Canonical Orderbook transformed."

# -----------------------------------------------------------------------------
# Stage 5: Transform Canonical (trade)
# -----------------------------------------------------------------------------
echo "[Stage 5/6] Transforming Trade stream to Canonical format..."
"$PYTHON" -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir "$CANONICAL_DIR" \
    --exchange "$EXCHANGE" \
    --stream "trade" \
    --schema-version "2.1.0" \
    --epoch-manifest "$EPOCH_MANIFEST"
echo "✓ Stage 5 Complete: Canonical Trade transformed."

# -----------------------------------------------------------------------------
# Stage 6: Partition Dataset
# -----------------------------------------------------------------------------
echo "[Stage 6/6] Partitioning Dataset with Embargo Windows..."
"$PYTHON" -m bithumb_coin_trader.research_cli partition-dataset \
    --canonical-manifest "$CANONICAL_MANIFEST" \
    --exchange "$EXCHANGE" \
    --market "$MARKET" \
    --stream "orderbook" \
    --output-dir "$DATASET_DIR" \
    --dq-report "$DQ_QUALIFICATION" \
    --source-manifest "$EPOCH_MANIFEST" \
    --deep-audit-report "$DEEP_DQ_REPORT" \
    --train-frac 0.60 \
    --val-frac 0.20 \
    --purge-window-ms 900000 \
    --clock receive_wall_clock

echo "==============================================================================="
echo "✓ 72H OFFLINE IMPORT PIPELINE COMPLETED SUCCESSFULLY"
echo "  Dataset sealed at: $DATASET_DIR"
echo "==============================================================================="
