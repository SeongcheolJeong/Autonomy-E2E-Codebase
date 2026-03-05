# Reference Migration Map (Weak Blocks)

## Local Reference Root

- `references/_reference_repos`
- Inventory: `30_Projects/P_E2E_Stack/prototype/reference_repo_inventory.json`

## Downloaded References (Pinned)

| Repo | Commit | Primary Use |
|---|---|---|
| `apollo` | `40c8a0127adb0bfdf32e03d412969575e8ab861a` | planning/control architecture patterns |
| `autoware` | `b4c171b3d643321deb1af5831cc9de164b364364` | ROS2 integration, planning/control/map pipeline |
| `awsim` | `08fdcc67a43c3c3c43d7f7dd8eeffb91d050ee39` | runtime rendering/runtime integration |
| `carla` | `fc52f323c1f05d615f0dce0e250bb235c8d8d39b` | rendering/sensor runtime/contracts |
| `scenario_runner` | `94ff3b8af752bad2b9d464ad5105868906aa34c0` | scenario execution/evaluation loop |
| `openpilot` | `ca5234a32f9b0331b11ed33f418a82669c5ac8e1` | vehicle interface/control/safety gating |
| `lanelet2` | `2d95b7d8e50eb9032256b2f41d2951a2bc6b0511` | map model/routing/validation |
| `sumo` | `39fee3a49df6bc255fc6eb72893546517bdca982` | traffic simulation/actor behavior generation |
| `esmini` | `77b4bbc00cb8b4e74e6659526f6c4e45703b1bc1` | OpenSCENARIO runtime compliance |
| `scenariogeneration` | `8c964333cfb575ad80105008a6f3e83bcbd69116` | OpenSCENARIO authoring/generation |
| `libopendrive` | `c3a5c8c8a3a16483f6a8ae9efb6fcb096f7ce2fb` | OpenDRIVE parser/conversion |

## Weak Block -> Source Mapping

1. Runtime Rendering / Execution Fidelity
- Weak point: runtime lane는 실행 가능성 체크 중심이고, 고충실도 렌더링/평가 루프가 약함.
- Sources:
- `carla/PythonAPI`, `carla/LibCarla`, `scenario_runner/srunner`
- `awsim/Assets`, `awsim/docs`
- Target modules:
- `30_Projects/P_Sim-Engine/prototype/sim_runtime_adapter_stub.py`
- `30_Projects/P_Sim-Engine/prototype/sim_runtime_probe_runner.py`
- Migration goal:
- 런타임 adapter를 “probe-only”에서 “scenario-run + result contract” 단계로 확장.

2. Scenario Authoring / Standards Compatibility
- Weak point: 시나리오 생성/교환 포맷 표준 대응이 제한적.
- Sources:
- `scenariogeneration/scenariogeneration`
- `esmini/EnvironmentSimulator`, `esmini/resources`
- `scenario_runner/Docs`
- Target modules:
- `30_Projects/P_Cloud-Engine/prototype/generate_batch_from_catalog.py`
- `30_Projects/P_Sim-Engine/prototype/examples/`
- Migration goal:
- OpenSCENARIO 기반 시나리오 import/export contract 추가.

3. Vehicle Dynamics / Control Realism
- Weak point: dynamics/control 블록이 스텁/경량 검증 위주.
- Sources:
- `openpilot/selfdrive/controls`, `openpilot/opendbc`
- `autoware/src`
- `apollo/modules/control`, `apollo/modules/planning`
- Target modules:
- `30_Projects/P_Sim-Engine/prototype/vehicle_dynamics_stub.py`
- `30_Projects/P_E2E_Stack/prototype/run_e2e_pipeline.py`
- Migration goal:
- 제어출력-동역학 응답 계약을 고정하고 phase3 요약 지표를 실제 제어 품질 기준으로 확장.

4. Map Stack (Lane Topology / Routing)
- Weak point: map 변환/검증은 존재하나 lane-level routing semantics가 약함.
- Sources:
- `lanelet2/lanelet2_core`, `lanelet2/lanelet2_routing`, `lanelet2/lanelet2_validation`
- `libopendrive/include`, `libopendrive/src`
- Target modules:
- `30_Projects/P_Map-Toolset-MVP/prototype/convert_map_format.py`
- `30_Projects/P_Map-Toolset-MVP/prototype/validate_canonical_map.py`
- Migration goal:
- OpenDRIVE -> internal canonical -> routing validation 체인 구축.

5. Traffic Scale / Multi-Actor Behavior
- Weak point: 대규모 actor/traffic 패턴 생성이 제한적.
- Sources:
- `sumo/tools`, `sumo/src`, `sumo/tests`
- Target modules:
- `30_Projects/P_Cloud-Engine/prototype/cloud_batch_runner.py`
- `30_Projects/P_Sim-Engine/prototype/augment_log_scene.py`
- Migration goal:
- batch 생성 단계에서 SUMO 기반 actor behavior profile을 주입.

## Migration Backlog (User-Visible Priority)

1. `runtime_failure_reason` surfaced execution summary
- Why: 운영자가 실패 이유를 바로 구분 (`runtime_command_failed` vs `runtime_evidence_missing`)
- Deliverable: runtime lane summary/notification에 reason counts 노출

2. CARLA scenario-run contract (non-probe)
- Why: “실행 가능”이 아니라 “시나리오 실행/결과 산출 가능” 체감
- Deliverable: 최소 1개 OpenSCENARIO-like case 실행 + report JSON

3. Lanelet2-backed map validation extension
- Why: map block 신뢰도 체감 개선
- Deliverable: topology/routing consistency rule 추가 + 실패 예시 리포트

4. SUMO actor-profile injection
- Why: 시나리오 다양성/규모 확장 체감
- Deliverable: batch profile에 SUMO-derived actor set 주입 경로

5. openpilot/autoware control-contract adapter
- Why: dynamics/control realism 체감
- Deliverable: phase3 vehicle dynamics summary에 controller error envelope 지표 추가

## Guardrails

- 라이선스는 각 repo top-level `LICENSE*` 기준으로 검토 후 코드 이식.
- 무분별한 전체 복사 금지: 계약/인터페이스 중심으로 최소 경로 이식.
- 이식 전후 동일 입력에 대해 리포트 schema diff 테스트를 필수로 추가.
