"""
scheduler/oci_usage_monitor.py

OCI 프리티어 사용량/한도 주간 점검 -> 텔레그램 알림.
DB 저장 없음 (휘발성, 조회 -> 알림 -> 종료).

인증: Instance Principal (이 VM 자체를 principal로 사용, API 키 파일 불필요).
사전 설정: Dynamic Group(asset-cloud-vm-dg) + Policy(asset-cloud-usage-monitor-policy)
콘솔에서 생성 완료 (2026-07-15).

각 조회 항목의 신뢰도가 서로 다르므로, 항목별로 근거를 주석에 명시한다:

1. Storage (Block/Boot Volume 200GB): oci limits resource-availability API가
   free-tier 한도 자체를 알고 있는 전용 리소스(total-free-storage-gb-regional).
   used+available = 200 정확히 일치 검증됨 (2026-07-15, used=47/available=153).
   -> 신뢰 가능, 그대로 사용.

2. Compute (A1 Flex OCPU/메모리): 같은 API의 available 값은 free-tier 한도가 아니라
   테넌시 원시 서비스 한도(약 1억)를 반환함이 확인됨 -> 무료 여부 판단에 쓸 수 없음.
   게다가 "PAYG 계정이 4 OCPU/24GB를 계속 무료로 쓸 수 있는지"는 Oracle 공식 문서와
   서포트 답변이 서로 모순되어 확인 불가 상태 (2026-07-15 기준).
   -> used 값만 정보성으로 표시하고, 무료/과금 여부 판단에는 쓰지 않는다.

3. 실제 청구 비용 (Usage API, COST): 정책 해석과 무관하게 사실 그대로인 유일한 지표.
   $0을 초과하는 서비스가 있으면 그 자체가 알림 대상.
   -> 과금 여부의 최종 판단 기준.

4. 아웃바운드 트래픽 (oci_vcn/VnicToNetworkBytes): 10TB 무료 한도의 정확한 리셋 기준
   (달력월 vs 청구주기)을 문서에서 확인하지 못함 (2026-07-15).
   -> 참고용 근사치로만 표시 ("이번 달 1일~현재" 구간 합계, 자체 정의한 구간).
"""

import datetime
import logging
import os
import sys
from zoneinfo import ZoneInfo

import oci

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from common.notify import notify_telegram_alert  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("oci_usage_monitor")

TENANCY_ID = "ocid1.tenancy.oc1..aaaaaaaanxdsvvocuxfx7bxvllj2qjsdumxenfjntvafyt2ylar3lzg4gmtq"
REGION = "ap-tokyo-1"
KST = ZoneInfo("Asia/Seoul")


def _get_signer_and_config():
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    config = {"region": REGION}
    return signer, config


def get_storage_status(signer, config):
    """Block/Boot Volume 프리티어 200GB 한도 대비 사용량 (used, available) in GB."""
    client = oci.limits.LimitsClient(config=config, signer=signer)
    resp = client.get_resource_availability(
        compartment_id=TENANCY_ID,
        service_name="block-storage",
        limit_name="total-free-storage-gb-regional",
    )
    return resp.data.used, resp.data.available


def get_compute_usage(signer, config):
    """A1 Flex 현재 사용량 (core, memory_gb). 정보성 표시 전용, 무료 한도 판단에 쓰지 않음."""
    client = oci.limits.LimitsClient(config=config, signer=signer)
    core = client.get_resource_availability(
        compartment_id=TENANCY_ID,
        service_name="compute",
        limit_name="standard-a1-core-regional-count",
    ).data.used
    memory = client.get_resource_availability(
        compartment_id=TENANCY_ID,
        service_name="compute",
        limit_name="standard-a1-memory-regional-count",
    ).data.used
    return core, memory


def get_monthly_cost_by_service(signer, config, month_start_utc, now_utc):
    """이번 달 서비스별 실제 청구 비용. $0 초과 항목만 반환."""
    client = oci.usage_api.UsageapiClient(config=config, signer=signer)
    details = oci.usage_api.models.RequestSummarizedUsagesDetails(
        tenant_id=TENANCY_ID,
        time_usage_started=month_start_utc,
        time_usage_ended=now_utc,
        granularity="MONTHLY",
        query_type="COST",
        group_by=["service"],
    )
    resp = client.request_summarized_usages(details)
    billed = []
    for item in resp.data.items:
        amount = item.computed_amount or 0
        if amount > 0:
            billed.append((item.service or "unknown", amount))
    return billed


def get_monthly_egress_gb(signer, config, month_start_utc, now_utc):
    """이번 달(자체 정의 구간) 아웃바운드 트래픽 근사치, GB 단위."""
    client = oci.monitoring.MonitoringClient(config=config, signer=signer)
    details = oci.monitoring.models.SummarizeMetricsDataDetails(
        namespace="oci_vcn",
        query="VnicToNetworkBytes[1d].sum()",
        start_time=month_start_utc,
        end_time=now_utc,
        resolution="1d",
    )
    resp = client.summarize_metrics_data(
        compartment_id=TENANCY_ID,
        summarize_metrics_data_details=details,
    )
    total_bytes = 0.0
    for series in resp.data:
        for point in series.aggregated_datapoints:
            total_bytes += point.value or 0.0
    return total_bytes / (1024 ** 3)


def build_message(storage, compute, billed, egress_gb, errors):
    used_gb, available_gb = storage
    core, memory = compute

    lines = ["📊 OCI 프리티어 주간 점검"]

    if used_gb is not None:
        total_gb = used_gb + available_gb
        pct = round(used_gb / total_gb * 100) if total_gb else 0
        lines.append(f"* Storage : {pct}% ( {used_gb}GB/{total_gb}GB )")
    else:
        lines.append("* Storage : 조회 실패")

    if egress_gb is not None:
        limit_gb = 10 * 1024  # 10TB, 참고용 상수 (정확한 리셋 시점 미확인)
        pct = round(egress_gb / limit_gb * 100, 1)
        lines.append(f"* Traffic : {pct}% ( {egress_gb:.1f}GB/{limit_gb}GB )")
    else:
        lines.append("* Traffic : 조회 실패")

    if core is not None:
        lines.append(f"* A1 Flex : {core} OCPU / {memory}GB")
    else:
        lines.append("* A1 Flex : 조회 실패")

    lines.append("")
    if billed:
        lines.append("⚠️ 이번 달 청구 발생:")
        for service, amount in billed:
            lines.append(f"  · {service}: ${amount:.2f}")
    else:
        lines.append("✅ 이번 달 청구 비용: $0")

    if errors:
        lines.append("")
        lines.append("❌ 조회 실패 항목:")
        for e in errors:
            lines.append(f"  · {e}")

    return "\n".join(lines)


def main():
    now_kst = datetime.datetime.now(KST)

    # Usage API는 timeUsageStarted/timeUsageEnded가 UTC 기준 00:00:00 정밀도여야 함
    # (KST 자정을 UTC로 변환하면 15:00:00이 되어 요구조건을 못 맞춤 -> UTC 기준으로 직접 계산)
    today_utc_midnight = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    month_start_utc = today_utc_midnight.replace(day=1)
    now_utc = today_utc_midnight + datetime.timedelta(days=1)  # 오늘 하루치까지 포함

    signer, config = _get_signer_and_config()

    storage = (None, None)
    compute = (None, None)
    billed = []
    egress_gb = None
    errors = []

    try:
        storage = get_storage_status(signer, config)
    except Exception as e:
        log.error(f"Storage 조회 실패: {e}")
        errors.append(f"Storage 조회 실패: {e}")

    try:
        compute = get_compute_usage(signer, config)
    except Exception as e:
        log.error(f"Compute 조회 실패: {e}")
        errors.append(f"Compute 조회 실패: {e}")

    try:
        billed = get_monthly_cost_by_service(signer, config, month_start_utc, now_utc)
    except Exception as e:
        log.error(f"비용 조회 실패: {e}")
        errors.append(f"비용 조회 실패: {e}")

    try:
        egress_gb = get_monthly_egress_gb(signer, config, month_start_utc, now_utc)
    except Exception as e:
        log.error(f"트래픽 조회 실패: {e}")
        errors.append(f"트래픽 조회 실패: {e}")

    message = build_message(storage, compute, billed, egress_gb, errors)
    log.info(message)
    notify_telegram_alert(message)


if __name__ == "__main__":
    main()