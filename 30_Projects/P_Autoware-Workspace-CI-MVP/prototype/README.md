# P_Autoware-Workspace-CI-MVP Prototype (v0)

Initial Phase-4 `hil_sim` scaffold for stack-level parity work.

## Files

- `hil_sequence_runner_stub.py`: validate HIL interface + test sequence and emit schedule manifest
- `examples/hil_interface_v0.json`: sample data interface contract
- `examples/hil_test_sequence_v0.json`: sample trigger/action sequence contract

## Smoke run

```bash
python3 hil_sequence_runner_stub.py \
  --interface examples/hil_interface_v0.json \
  --sequence examples/hil_test_sequence_v0.json \
  --out runs/hil_schedule_manifest_v0.json
```
