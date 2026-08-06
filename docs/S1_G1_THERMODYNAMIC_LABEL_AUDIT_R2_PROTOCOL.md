# S1 G1 第三 smearing / 稠密 k 点热力学标签审计 R2 协议

状态：`protocol_frozen`（正式执行状态以已提交的 R2 config、逐点证据和
summary 为准）
协议日期：`2026-08-06`
范围：仅继续 G1 “第三 smearing / 稠密 k 点热力学标签”子项。

R2 不追溯改判 R1，也不会将有限温度量冒充为严格零温标签。它的任务是：
保留 R1 已经严格验收的 10 个点，保留 R1-034 中断事件的原始失败链，
用 30 个全新 ID 完成同一个 40 点逻辑矩阵。

## 1. R1 事实边界与 R2 修订原因

R1 的不可变预注册边界为：

- preregistration commit：`f71dd6b0fca238c386c0203b077ebf426e6b6926`；
- config SHA-256：
  `76873a782a21fb45cb96f318dee992ea5f9ac25625c066d691806fafd6450eba`；
- manifest SHA-256：
  `7650fe3e3f528c8e12919156ae5f8475cfc8963bcefe14137a648e7cd2859d6c`；
- 完整 R1 input-root tree OID：
  `39ec363adf9cea8fbd7593669f8e268b326e496c`。

R1 已接受 10 个点，且每个 run tree 自引入提交后保持不变：

```text
024, 036, 031, 039, 021, 035, 027, 037, 028, 038
```

它们已完成 Al/Mg 的 V0 k 点 pilot，并完成除 Mg 1.10 V0 以外的五组
common-quarter / extra-dense 端点。R2 必须用 R1 validator 重验这 10 个点，
并在 config 中锁定每个点的 introduction commit、run-tree OID、R1 input-tree
OID 与关键证据 blob。任一复用点不能通过重验，R2 均不得启动新计算。

R1-034 的 ABACUS SCF 和内层 MPI audit 完成，但 SSH/PTY 上的 host
orchestration 在 host postflight 之前中断，因而缺少完整 host 证据。R1 已将其
权威分类为 `indeterminate / workflow_or_runtime_capability_failure`，失败提交为
`df57f9b610d82d75835193f84b4bfbb4ffa5007b`，相邻归档提交为
`b0b7db592b3438322289dbd98cf66686c6f557a4`，两侧 tree OID 同为
`c0b7d1cbdfea594c130f086b29f898102d441383`。该尝试对 R2 验收分母贡献为
0，不得事后改判为 accepted，也不得在 R1 ID 034 上重试。

## 2. 科学问题、热力学语义与数值设置

R2 完全继承 R1 的科学问题、物理输入、数值轴和所有阈值。不得修改
ABACUS、伪势、XC、平面波截断、SCF 设置、结构、体积比、smearing、k 网格、
cube 精度、runtime/KMP 契约或统计门。

- 体积比：`0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10`；
- 标准、二分之一和四分之一 smearing：
  `0.00734986, 0.00367493, 0.001837465 Ry`；
- common-dense k 网格：Al `28x28x28`，Mg `24x24x16`；
- extra-dense quarter k 网格：Al `32x32x32`，Mg `28x28x18`；
- 每点 4 MPI ranks，`out_chg 1 17`，`out_pot 1 17`；
- 标量 EOS 观测量仍为有限 smearing 的熅校正估计量
  `E_ec = F - m/2`，而不是精确 0 K 能量；
- 权威场标签是有限温度 Mermin bundle
  `{rho_sigma(r), F_s^sigma, g_sigma(r)}`，其中
  `g_sigma = P_N[mu_sigma-v_eff_sigma]`。

R2 config 中的 `numerical_axes`、`output_contract`、
`thermodynamic_semantics`、`acceptance`、`runtime`、`runtime_audit`、
`kmp_contract` 和 `rank_count` 必须与冻结 R1 config 逐字节等价。

## 3. 40 点逻辑矩阵与 30 个新 ID

R2 最终分析仍使用 R1 定义的 40 个逻辑槽位。有效 ID 由下列映射唯一确定。

### 3.1 复用的 10 个 R1 accepted 槽位

| 逻辑槽位 | 有效证据 ID | 证据来源 |
|---|---|---|
| 021, 024, 027, 028, 031 | 同号 R1 ID | R1 accepted common-quarter |
| 035, 036, 037, 038, 039 | 同号 R1 ID | R1 accepted extra-dense quarter |

### 3.2 新执行的 30 个槽位

| R2 新 ID | R1 逻辑槽位 | 用途 |
|---|---|---|
| 041 | 034 | Mg 1.10 common-dense quarter，替代 R1 中断槽位 |
| 042 | 040 | Mg 1.10 extra-dense quarter，与 041 完成 k gate |
| 043–048 | 001–006 | Al/Mg 三端点 dense-standard field replay |
| 049–055 | 007–013 | Al 七点 common-dense half EOS |
| 056–062 | 014–020 | Mg 七点 common-dense half EOS |
| 063, 064 | 022, 023 | Al 0.94/0.97 common-dense quarter |
| 065, 066 | 025, 026 | Al 1.03/1.06 common-dense quarter |
| 067, 068 | 029, 030 | Mg 0.94/0.97 common-dense quarter |
| 069, 070 | 032, 033 | Mg 1.03/1.06 common-dense quarter |

R2 新 ID 的固定执行顺序是 `041, 042, 043, ..., 070`。配置中必须同时保留
40 行 `logical_run_matrix` 和 30 行 `new_run_matrix`，分析器只能通过该映射
聚合 R1 与 R2 证据，不得拷贝、改名或假装生成新的 R1 run tree。

## 4. 输入的机械派生和预注册边界

R2 generator 必须从 R1 preregistration commit 中直接读取每个逻辑槽位的
`INPUT`、`STRU`、`KPT`、`metadata.json` Git blob，而不是从工作树或历史
run 重建科学输入。机械派生规则是：

1. `STRU` 和 `KPT` 必须逐字节相同；
2. `INPUT` 只允许 `suffix` 从 `g1tlr1_*` 改为 `g1tlr2_*`；
3. `metadata.json` 只允许改动
   `protocol_revision`、`experiment_id`、`suffix` 三个 key；
4. metadata 中的 R1 `dataset_kind` 保留为输入来源证据，执行协议归属由
   R2 config 和新 protocol revision 确定；
5. manifest 恰有 30 行，表头与 R1 `MANIFEST_FIELDS` 完全相同；
6. 逻辑参照 ID 仍保留在 TSV 中，它们的有效证据 ID 由 config 的逻辑矩阵
   fail-closed 解析。

正式 preregistration 之前，R2 config、manifest、input root、30 个 active run
prefix、30 个 failed archive prefix 以及 30 个 attempt-ledger marker 必须全部不存在。
实现必须先在父提交冻结；随后的 preregistration commit 只能新增：

- `config/S1_g1_thermodynamic_label_audit_r2.json`；
- `config/S1_g1_thermodynamic_label_audit_r2_manifest.tsv`；
- `inputs/s1/g1_thermodynamic_label_audit_r2/` 下的 120 个注册 blob。

预注册提交中不得出现 run、failure archive、detachment attestation 或 attempt
ledger 证据。

## 5. 执行阶段与屏障

### P1 continuation：完成 Mg 1.10 k gate

R1 的 P0 V0 pilot 已由 10 个复用点中的 `024/036` 和 `031/039` 完成。
R2 首先且只能执行：

```text
S1-20260806-041  logical 034  Mg 1.10 common-dense quarter
S1-20260806-042  logical 040  Mg 1.10 extra-dense quarter
```

041 与 042 是 R2 的双 pilot，同时是 R1 P1 的唯一未完成组。两点都必须逐点
accepted，且与 10 个复用点组成的六组 common/extra field 和两条三端点
k 曲线必须全部通过。在此之前，043–070 的 run prefix、archive prefix 和
attempt marker 全部禁止出现。

### P2：完成热力学标签矩阵

k gate 通过后，严格按 043–070 顺序执行。每个点必须完成“正式尝试
marker commit → solver/runtime audit → parser/core validator → 单点 run introduction
commit”的因果链。run introduction commit 的直接父提交必须就是该 ID 的
marker commit。

执行在每个能被早期硬门完整判定的边界调用已提交 validator。任一阶段
不完整、indeterminate 或 rejected 都必须立即保留失败、相邻归档并停止。

所有被 runner 用作放行条件的 validator/analyzer 命令都是正式 barrier command，
不得依赖 shell 的裸 `set -e` 丢失失败上下文。任一 barrier command 返回非零时，
runner 必须先在下列冻结目录写入一个不可覆盖的机器可读 JSON：

```text
orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/barrier_failures/
```

该 JSON 必须记录 barrier 名、物理/逻辑 ID（全局 barrier 可为 `null`）、完整命令参数、
非零退出码、config/manifest 与 supervisor launch 哈希及失败前 Git HEAD；随后以只包含
该 JSON 的 exact-scope commit 保存失败并立即停止。既不能越过失败 barrier 继续提高完成率，
也不能在当前 revision 内重跑相同 barrier 或 solver。冻结 key set 为：

```text
schema_version, protocol_revision, status, created_utc, barrier_name,
experiment_id, logical_experiment_id, command_argv, exit_code, config_path,
config_sha256, manifest_path, manifest_sha256, git_head_before_failure,
supervisor_state_directory, supervisor_launch_path, supervisor_launch_sha256,
retry_policy
```

其中 `status=barrier_failed`、`exit_code != 0`、
`retry_policy=stop_after_exact_scope_commit_no_continue_or_retry`。
该 terminal failure commit 之后仅允许线性追加
`docs/M_OFDFT_项目进度与交接.md` 的交接记录；任何 run、attempt、analysis、配置或其他路径
变化都视为越过失败门继续执行。

## 6. SSH/PTY 脱离、GO gate 和不可重试尝试账本

R1-034 暴露的是 host orchestration 生命周期问题，因此 R2 的外层监督进程是正式
证据边界的一部分，但不修改内层科学/runtime 合同。

所有会改变仓库或 supervisor state 的 launcher 命令必须从下列精确的 10 键
ambient environment 启动；不得继承登录 shell、SSH daemon、user site 或 shell startup
环境：

```text
HOME=/home/shenwei01
LC_ALL=C
LOGNAME=shenwei01
PATH=/usr/bin:/bin
PYTHONHASHSEED=0
PYTHONIOENCODING=UTF-8
PYTHONNOUSERSITE=1
PYTHONUTF8=1
TZ=UTC
USER=shenwei01
```

config 必须登记这一 exact key/value map 及其 compact sorted canonical JSON SHA-256
`ef6a2022dbcc38b64c80ac9715ed0d52a73bcdbcf7d07ca9610faacd106bd6b6`。任意多余键
（特别是 `BASH_ENV`/`ENV`/`PYTHONPATH`）、缺键或值变化都必须在任何写入之前
fail closed。所有 Python 调用使用登记 Python 的绝对路径并显式带 `-s`；
launcher 还必须核对当前解释器与登记 Python 的 realpath/SHA-256。validator 和
detached supervisor 子进程显式传入这 10 键，runner 则只能额外获得 6 个已登记的
supervisor binding 变量。runner 不通过 shebang/PATH 选择 shell，必须由经
path/realpath/SHA-256 重验的登记绝对 Bash 路径启动。`launch.json.environment`
必须记录同一 exact keys/values/canonical hash 结构；所有 mutating launcher 入口及
supervisor 的 `umask` 必须精确为 `0022`。标准命令前缀固定为：

```bash
umask 0022
/usr/bin/env -i HOME=/home/shenwei01 LC_ALL=C LOGNAME=shenwei01 \
  PATH=/usr/bin:/bin PYTHONHASHSEED=0 PYTHONIOENCODING=UTF-8 \
  PYTHONNOUSERSITE=1 PYTHONUTF8=1 TZ=UTC USER=shenwei01 \
  /usr/bin/python3 -s scripts/launch_s1_g1_thermodynamic_label_audit_r2.py ...
```

- 外部单次 state directory 固定为
  `/home/shenwei01/.local/state/m_ofdft/g1_thermodynamic_label_audit_r2_20260806`；
- 监督进程必须使用新 session，stdin 为 `/dev/null`，stdout/stderr 指向固定日志，
  并通过非阻塞 `flock` 证明唯一 writer；
- 监督进程必须先写入并 fsync `launch.json` 与 append-only journal，再等待 GO；
- `launch.json` 必须使用冻结的 exact key set，并逐项绑定 schema/status、launch method、
  restart policy、project/working directory、hostname、`0022` umask、environment、state/lock/log
  路径、boot/process/Git 身份、registered files、runner argv 与 UTC；其 `launcher` 子对象必须
  精确绑定官方 launcher 路径/SHA-256 及登记 Python 的 path/realpath/SHA-256；
- supervisor 必须以同一次 `O_NOFOLLOW` 稳定读取获得 runner、manifest 和 config 的原始字节及
  SHA-256，再分别复制到 Linux sealed memfd `200/201/202`。三者最终 seal mask 必须精确为
  `15 = F_SEAL_SEAL|F_SEAL_SHRINK|F_SEAL_GROW|F_SEAL_WRITE`；`launch.json` 的
  `sealed_execution_inputs` 必须登记模式、固定 FD、`/proc/self/fd/*` 路径、canonical provenance
  路径、SHA-256、seal 名称/mask 与 `pass_fds`。登记 Bash 实际执行 `/proc/self/fd/200`，所有科学
  config/manifest 内容只能从 `/proc/self/fd/202` 和 `/proc/self/fd/201` 读取；canonical 路径仅供
  Git/validator provenance 使用。GO 前 validator 必须从 supervisor 的 `/proc/<pid>/fd/200..202`
  独立复核字节哈希与 exact seals；runner 启动并继承三者后，supervisor 关闭自己的副本；
- launch/GO/terminal/detachment/completion 等单文件回执必须先在同目录临时文件完整写入并
  fsync，再以不覆盖既有目标的原子 hard-link 发布并 fsync 目录；观察者不得看到空白或部分
  JSON，任何同名目标已存在都必须 fail closed；
- 主机端必须用 PID/start-time/boot-id 证明还是同一进程，向其发送 SIGHUP，
  并在它继续存活且 journal 记录信号后生成
  `orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/detachment.json`；
- detachment attestation 必须独立提交，GO 必须绑定 launch hash、attestation hash、
  boot-id 和 supervisor start time；
- supervisor 在派生 runner 前必须对 GO 的 exact 13-key schema/status、全部登记文件、冻结
  attestation 路径/哈希及当前 Git HEAD 重新验真，并再次运行 committed detachment validator；
  第 13 个键 `sealed_execution_inputs_sha256` 必须绑定 `launch.json` 中上述 sealed record 的
  canonical SHA-256；
  监督进程必须固定单次稳定读取所得的 GO 原始字节 SHA-256，journal、Popen 环境和 runner
  preflight 只能复用该固定 SHA，禁止在校验后重新解释或重新绑定路径中的新字节；runner 还须
  在 solver 前独立复验 exact 13-key GO、attestation、当前 Git HEAD 与 journal 的固定哈希；
  最终完成复验必须证明 `GO.git_head`、detachment introduction commit 和
  `journal.go_accepted.git_head` 三者相等，且首个 attempt-marker commit 的 parent 正是
  该 GO HEAD；
- runner 只能由该监督进程作为仍存活的直接子进程派生；启动时必须同时核对 Bash PPID、
  supervisor PID/start-time/session/process-group/TTY、boot-id、launch hash 与 GO hash，任何手工
  调用、陈旧 state 或父进程替换都必须在 solver 前拒绝；
- 任一证明不完整时，禁止创建 GO，也禁止进入 solver。

每个新 ID 在 solver 前必须用 `O_CREAT|O_EXCL`、file fsync 和 parent-directory
fsync 同时创建外部 marker 和仓库内 marker：

```text
orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/attempts/<ID>.json
```

仓库 marker 必须单独 exact-scope commit，且 key set 恰为：

```text
schema_version, protocol_revision, experiment_id, logical_experiment_id,
status, retry_policy, created_utc, config_path, config_sha256, manifest_path,
manifest_sha256, git_head_before_attempt, supervisor_state_directory,
supervisor_launch_path, supervisor_launch_sha256, supervisor_pid,
supervisor_start_time_ticks, boot_id, supervisor_go_path,
supervisor_go_sha256, go_git_head
```

`status` 固定为 `formal_attempt_started`，`retry_policy` 固定为
`new_protocol_revision_and_new_experiment_ids_only`。任一外部或 Git marker 的存在都代表
该 ID 已消耗；若其后没有得到 immutable accepted run，也只能保留并启动新修订、
新 ID，不得重新调用 solver。
首个 marker 的提交 parent 必须同时等于 `GO.git_head` 与 detachment attestation 的唯一
introduction commit；runner 必须在任何 solver 进程创建前用 committed validator 验收该 marker。
因此，detachment 提交之后插入任何额外 clean commit 都会在首点计算前 fail closed，而不是留到
最终 completion 才发现。
其后每个 marker 的提交 parent 必须精确等于执行顺序中前一个 accepted run 的 introduction
commit；点间插入文档或其他 clean commit 同样会在下一个 solver 前拒绝。

监督进程正常结束也不能只靠外部 `terminal.json` 宣布整个 R2 协议 accepted。runner
可以在全部科学门通过后写出 `audit_status=accepted` 的科学 summary；这一状态只说明冻结的
科学与 runtime 分母通过。在 committed supervisor completion 出现前，整体协议状态仍严格是
`pending_supervisor_completion`。

launcher 的 `finalize` 动作必须在 runner 退出码为 0 且外部 terminal、launch、journal、
科学 summary 的身份全部复核后，独占生成：

```text
orchestration/s1/g1_thermodynamic_label_audit_r2_20260806/supervisor_completion.json
```

`finalize` 在消费这个不可覆盖路径之前，必须用不跟随符号链接的稳定读取一次性
绑定 launch、GO、terminal、journal、log、analysis、config 和 manifest 的原始字节与
SHA-256；terminal 与 journal 必须通过 exact key set、strict JSON integer、PID/start-time、
GO 事件和时序验证，launch/detachment 记录必须再次通过已提交 validator。写入前
还必须逐项复验原始字节未变、工作树仍 clean，且 Git HEAD 与首次对照 terminal 时
冻结的 HEAD 完全相同。正式模型要求所有合作进程遵守 single-writer/lock 协议；不遵守
锁且持续篡改同 UID 外部 state 的恶意进程不在可恢复性承诺内。

随后只以这一个路径创建 exact-scope completion commit。completion JSON 必须绑定
config/manifest、Git HEAD、supervisor PID/start-time/boot-id、launch/terminal/journal、
runner exit code 和 accepted 科学 summary，且 key set 恰为：

```text
schema_version, protocol_revision, status, created_utc, config_path,
config_sha256, manifest_path, manifest_sha256, git_head_before_completion,
supervisor_state_directory, supervisor_launch_path, supervisor_launch_sha256,
supervisor_terminal_path, supervisor_terminal_sha256, supervisor_journal_path,
supervisor_journal_sha256, supervisor_pid, supervisor_start_time_ticks, boot_id,
runner_exit_code, analysis_path, analysis_sha256, analysis_audit_status,
final_acceptance_policy
```

固定值为 `status=supervisor_completed`、`runner_exit_code=0`、
`analysis_audit_status=accepted`、
`final_acceptance_policy=committed_completion_then_validator_revalidation`。
completion commit 之后还必须再次运行 committed validator；只有这次重验接受，整体协议才
能由 `pending_supervisor_completion` 转为最终 accepted。
最终验收后的 Git 后继提交只允许线性修改
`docs/M_OFDFT_项目进度与交接.md`，用于兑现持续交接要求；该白名单不允许新增或改写任何
科学、runtime、配置、attempt、barrier 或 completion 证据。

## 7. 不变的量化硬门

所有不等式均为严格不等式，等于阈值即失败。R2 不新增事后阈值，也不降低
R1 标准。

### 7.1 完整性、标量与 EOS

- 逻辑注册点 accepted `40/40`：R1 reused `10/10` + R2 executed `30/30`；
- 收敛且有完整 finite thermodynamic label 的逻辑点 `40/40`；
- 标量点 `42/42`：14 个 R8 dense-standard source + 14 half + 14 quarter；
- 六个 7 点 BM3 fit 都要满足：平衡体积严格在取样区间内，`B0 > 0`，
  最大拟合残差 `< 1 meV/atom`；
- Al/Mg 的 `standard -> half` 和 `half -> quarter` 四组相邻精炼都要满足：
  V100 锚定原始曲线最大差 `< 2 meV/atom`，平衡体积相对变化 `< 0.2%`；
- 六个 dense-standard field replay 都要满足
  `|delta E_ec| < 0.1 meV/atom`、`|delta F| < 0.1 meV/atom`、
  `|delta P| < 0.02 GPa`。

### 7.2 电子数、热力学恒等式与场标签

- 每个逻辑点的独立 cube 积分电子数相对误差 `< 1e-10`；
- `U=F-m`、`E_ec=F-m/2`、`T_sU=E_one_elec-E_localpp`、`F_s=T_sU+m`
  及总自由能分解的单原子残差均 `< 1e-8 eV/atom`；
- 14 组 half-to-quarter field pair 和 6 组 common-quarter-to-extra-dense field pair
  全部通过；
- 每对均要 `D1 < 0.005`、`D2 < 0.005`、`Dg < 0.01`、
  `RMS_g < 0.005 eV`；
- Al/Mg 的 V0 common/extra 绝对 `E_ec` 差 `< 2 meV/atom`；
- Al/Mg 的 0.90/1.00/1.10 三端点 common/extra V100 锚定曲线最大差
  `< 2 meV/atom`。

### 7.3 Runtime/KMP aggregate

复用点和新点共同组成与 R1 相同的逻辑分母：

```text
accepted runtime audits             40/40
accepted rank lifecycles           160/160
successful lifecycle syscalls      480/480
```

successful old-prefix access/execution/mapping、unknown probe、unexpected mapping、
unhashed accepted mapping、incomplete lifecycle、duplicate-rank evidence、ambiguous exec、
counterpart missing/byte mismatch 均必须为 0。

## 8. 失败保留、最终判决与交接

每个 accepted R2 run 独立提交。首个失败必须先以独立 commit 保留 active run
及机器可读状态，再用相邻 commit 移入唯一
`failed_runs/runtime_relocation/<ID>/attempt-<failure_commit[:12]>`，然后停止。
分类和 inventory 必须使用 R2 专属名，不得覆盖 R1 证据。

最终 analyzer 必须显式报告 `R1 reused=10`、`R2 executed=30`、
`R1-034 failed archive contribution=0`，并为 completion、source/input integrity、SCF、
thermodynamic identity、electron number、EOS fit、adjacent smearing、equilibrium volume、
replay equivalence、density、derivative、k gate、runtime/KMP 分别输出失败 ID 列表。
所有列表为空且所有精确分母匹配，只允许科学 summary 标记 `audit_status=accepted`；在
supervisor completion 未提交时，整体仍是 `pending_supervisor_completion`。只有 completion
exact-scope commit 存在、内容严格通过且 committed validator 再次重验接受时，R2 整体才可
标记最终 `accepted`。

accepted 仅授权：

1. 将 common-dense `sigma/4` 的有限温度 Mermin bundle 记为当前低 smearing
   参考；
2. 关闭这一个 G1 子项，使总体 G1 成为 `pending (2/6)`；
3. 把完整 config/manifest、逻辑映射、逐点提交、监督进程 journal、尝试账本、
   summary 和进度 MD 交给下一执行者。

它不授权精确 0 K 声明、S2/ML、第二 OF 交叉代码审计、KS-NL -> KS-L ->
OF-L 检查、位移/应变参考或十案例单命令再生成门。
