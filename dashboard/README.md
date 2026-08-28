# 퀀트 운영 대시보드 UI v0.1

향후 V9.x와 AWS 모니터링 연결을 위한 frontend-only 기반이다. Python collector 및 trading runtime과 의도적으로 분리되어 있다.

## 안전 경계

- 표시 값은 모두 정적 mock fixture다.
- collector, 계좌, 주문, API key, AWS resource, trading backend와 연결되지 않았다.
- 실거래와 신규 진입은 읽기 전용 비활성 상태이며 override 제어가 없다.
- 없는 trading data는 0이 아니라 `제공 안 됨`으로 표시한다.
- 입증할 수 없는 V9 지표는 `검증 불가`로 표시한다.
- 이 UI는 운영 source of truth가 아니며 실제 collector/trading 상태를 판정하지 않는다.

## 로컬 실행

```sh
npm install
npm run dev
```

## 검증

```sh
npm run typecheck
npm run lint
npm test
npm run build
```

현재 UI는 개요, 수집기 상태, 안전 센터, 로그/이벤트를 제공한다. 트레이딩, 성과, 연구실, AWS/인프라는 이후 evidence-gated integration을 위한 placeholder다.
