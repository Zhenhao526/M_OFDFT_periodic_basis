# S1/G1 三层验证 scientific-rejection 处置协议（analysis-only R3）

协议版本：`S1-G1-THREE-LAYER-ANALYSIS-20260810-R3`。本修订不创建 solver ID、不写外部 state、不启动或重试任何计算，也不改变 R2 的任何科学门。

## 来源与目的

R2 continuation 在 accepted aggregate terminal 后必须先由原 R2 analyzer 完整 collect/replay，并照实提交 `summary.status=rejected` 的 scientific-rejection evidence。R2 的 `--require-committed` 只接受 scientific `accepted`，因此不能把“证据有效但科学门拒绝”误作编排失败，也不能放宽 `|ΔV0|<=0.5%` 来迁就结果。

R3 只读取已提交的 R2 analysis。它逐字冻结 R2 evidence commit、`summary.json`、`points.tsv`、`README.md`、terminal 与完整 analysis tree inventory；调用冻结 R2 analyzer 对 committed raw/terminal 做第二次完整 replay，要求 replay 输出与 R2 三个结果文件逐字一致。UPF 正文仍不进入 Git，replay 只从冻结 external cache 临时注入并核 SHA。

## 唯一可接受处置

R3 必须确认：R2 terminal accepted、8/8 formal continuation、failed=0、retried=0、aggregate RC=0；P0 recovery accepted；Al 三条 BM3 fit 均 accepted；KS-L↔KS-NL `|ΔB0|<=10%`；唯一 hard rejection 是 KS-L↔KS-NL `|ΔV0|>0.5%`。Mg 始终 diagnostic-only，不得影响 disposition。

上述全部成立时，R3 输出状态只能是 `evidence_valid_scientific_gate_rejected`，进程退出 0，表示“证据闭包有效，科学假设未过门”。这不是 scientific accepted，不关闭独立第二 KS/QE、端点复查、应变/相门或 G1 6/6。

若 raw/terminal replay、来源 Git/SHA、denominator 或预注册 rejection signature 任一不一致，R3 必须 fail-closed；不得把证据错误归类为科学拒绝。

