# S1/G1 三层验证 scientific-rejection 处置协议（analysis-only R3）

协议版本：`S1-G1-THREE-LAYER-ANALYSIS-20260810-R3`。本修订不创建 solver ID、不写外部 state、不启动或重试任何计算，也不改变 R2 的任何科学门。

## 来源与目的

R2 continuation 在 accepted aggregate terminal 后必须先由原 R2 analyzer 完整 collect/replay，并照实提交 `summary.status=rejected` 的 scientific-rejection evidence。R2 的 `--require-committed` 只接受 scientific `accepted`，因此不能把“证据有效但科学门拒绝”误作编排失败，也不能放宽 `|ΔV0|<=0.5%` 来迁就结果。

R3 只读取已提交的 R2 analysis。它逐字冻结 R2 evidence commit、`summary.json`、`points.tsv`、`README.md`、terminal 与完整 analysis tree inventory；调用冻结 R2 analyzer 对 committed raw/terminal 做第二次完整 replay，要求 replay 输出与 R2 三个结果文件逐字一致。UPF 正文仍不进入 Git，replay 只从冻结 external cache 临时注入并核 SHA。

## Capture 与 Git 闭包

R2 terminal accepted 后，必须先在独立 R3 capture worktree 完成两次提交：implementation commit 冻结 capture 程序，再以**仅修改本协议配置 JSON**的单父 prereg commit 写入 implementation SHA 与 capture-script SHA。Capture 程序要求自己运行于 `python3 -s -B`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONNOUSERSITE=1`，且 R2 worktree clean、精确位于 `3205d4972366c8a53a01fee2c5de22a4028b01eb`、analysis root 事前不存在。它逐文件核 R2 analyzer/config/manifest/parser/common/fit 依赖与该 commit 的 Git blob 一致，并核原 R2 acceptance 门与本协议逐值相等。

R2 analyzer 只能运行一次。预期 `--collect` 返回 2 且生成 `summary.status=rejected`；stdout/stderr 先写外部临时流文件，随后 capture 以 `O_EXCL` 在 analyzer 已生成的 tree 中恰好新增 `analysis.stdout`、`analysis.stderr`、`analysis_invocation.json` 三个 artifact。Invocation 必须冻结 exact argv/cwd、capture implementation/prereg/script blob、R2 dependency blobs、terminal SHA/size、最小隔离环境、exit=2、stream SHA/size 和新增 artifact 前的完整逐文件 inventory。analysis tree 中任何 UPF 文件或 UPF body signature 都 fail-closed。

R3 不信任 invocation 的自述：它从 Git 独立证明 capture prereg 的唯一父、仅配置差异、配置 blob 与 script blob，并要求 capture prereg 是 R3 analysis implementation 的祖先；invocation key 集合必须精确相等，UTC 起止可解析且与 monotonic duration 在 0.05 s 内一致，interpretation 必须逐字声明 exit 2 是科学门拒绝而非执行失败。

然后 R2 只提交该 analysis root，且 evidence commit 必须是 runner commit 的唯一子提交、diff 只含 analysis root。最终 R3 分支必须以这个 R2 evidence commit 为祖先；R3 implementation 后的 prereg commit 只能改配置来冻结 source tree/commit/invocation 身份。R3 analyzer 只能从 clean prereg 执行。最终 evidence commit 必须以 prereg 为唯一父提交，且 diff 恰好新增 R3 的 `README.md`、`gate_metrics.tsv`、`summary.json`。`--require-committed` 从 final 的唯一父反推 prereg，完整 replay 后逐字比较三个输出；因此不用也不允许在配置中伪造自指的 prereg commit SHA。

## 唯一可接受处置

R3 必须确认：R2 terminal accepted、8/8 formal continuation、failed=0、retried=0、aggregate RC=0；P0 recovery accepted；Al 三条 BM3 fit 均 accepted；KS-L↔KS-NL `|ΔB0|<=10%`；唯一 hard rejection 是 KS-L↔KS-NL `|ΔV0|>0.5%`。Mg 始终 diagnostic-only，不得影响 disposition。

上述全部成立时，R3 输出状态只能是 `evidence_valid_scientific_gate_rejected`，进程退出 0，表示“证据闭包有效，科学假设未过门”。这不是 scientific accepted，不关闭独立第二 KS/QE、端点复查、应变/相门或 G1 6/6。

若 raw/terminal replay、来源 Git/SHA、denominator 或预注册 rejection signature 任一不一致，R3 必须 fail-closed；不得把证据错误归类为科学拒绝。

R2 analyzer 的 exit 2 是预注册科学门拒绝，不是 solver/terminal failure；R3 validator 的 exit 0 只表示该拒绝结论被可信复现。任何一步都不得声称第二独立 KS/QE 已闭合或 G1 已达到 6/6。
