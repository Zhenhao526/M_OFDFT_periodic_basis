# S1-R8-RUNTIME-RELOCATION-R2 六点运行时重定位等价协议

状态：`generator_ready_not_frozen`。本文件名为历史兼容名称；正式协议不是“只改
MPI 前缀”，而是 **runtime-relocation equivalence**。当前提交没有创建正式
`config/S1_runtime_relocation_equivalence.json` 或
`config/S1_runtime_relocation_equivalence_manifest.tsv`。不得手写这两个文件，也
不得在 074 先行检查通过前启动正式 113–118。

## 1. 固定对象和六点矩阵

| replay | reference | 材料 | R8 系列 |
|---|---|---|---|
| S1-20260805-113 | S1-20260805-074 | Al | OFDFT next cutoff, v100 |
| S1-20260805-114 | S1-20260805-081 | Al | KSDFT next cutoff, v100 |
| S1-20260805-115 | S1-20260805-088 | Al | KSDFT next kmesh, v100 |
| S1-20260805-116 | S1-20260805-095 | Mg | OFDFT next cutoff, v100 |
| S1-20260805-117 | S1-20260805-102 | Mg | KSDFT next cutoff, v100 |
| S1-20260805-118 | S1-20260805-109 | Mg | KSDFT next kmesh, v100 |

六点逐字复用引用行的 `input_directory`；不得重新生成或调整输入。生成器冻结并验证
`INPUT`、`STRU`、`KPT`、`metadata.json`、赝势、引用 `result.json`、引用原始日志、
引用运行元数据及所有运行时身份。

ABACUS 的 reference 和 replay 是两个独立身份，不要求 SHA 相同：

- reference：由 074/081/088/095/102/109 的运行元数据读取，已登记 SHA-256
  `2d68a57c7b25608b3550854dabc2e63601eeca956bf185ad7d0967052bdbb4ba`；
- replay：
  `/home/shenwei01/M_OFDFT_recovery_S0_20260805_001/source/abacus_pw_para_relocated_20260805`，
  SHA-256
  `438c74b9ada4c8df15ffbb66da6755907dfd2a3812ecf868fafd4d7dc4db62e1`。

byte-level ELF 门要求两者 Build ID、ELF/program/section headers、LOAD 布局、
`NEEDED` 和除动态路径外的 dynamic section 一致。恰好 60 个不同字节只能位于
`.dynstr` 最末 RUNPATH 槽（零基偏移 23260–23319）；replay RUNPATH 必须恰为
`$ORIGIN/../conda_prefix/lib`，余量全为 NUL，且不得残留旧 RPATH/RUNPATH。
`/usr/bin/readelf` 和 `/usr/bin/chrpath` 的 path、realpath、SHA-256、版本输出均冻结；
`chrpath` 命令只作为登记的复现配方，最终权威是上述字节门。

reference/replay `mpirun` 与最终 `prterun` 分别冻结 path、realpath、SHA-256；两侧
对应二进制必须逐字相同。旧 R8 元数据未记录 mpirun 时，由
`--reference-mpirun` 显式冻结实际调用的 recovery mpirun，并明确保留这一历史
provenance limitation，不补写旧元数据。

## 2. 074 必须先行，但不占用正式 ID

在生成正式 config/manifest 之前，先用 **074 的冻结输入**完成一次非正式、受管的
namespace smoke。唯一标识为 `S1-RUNTIME-SMOKE-20260805-074`，唯一输出根为
`analysis/s1/runtime_relocation_smoke_20260805/`；单点入口只有同时收到显式 smoke
mode、固定标识和该绝对目录 override 才允许非标准 ID。它不得创建或改写引用 074
或正式 113–118，也不得把 `/tmp` 作为最终证据位置。专用执行入口为：

```bash
scripts/run_s1_runtime_relocation_smoke.py \
  --recovery-prefix /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix \
  --old-prefix /home/shenwei01/wt_melting_runtime_20260724/conda_prefix \
  --abacus /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/source/abacus_pw_para_relocated_20260805 \
  --mpirun /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix/bin/mpirun \
  --reference-mpirun /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix/bin/mpirun \
  --launcher /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix/bin/prterun \
  --reference-launcher /home/shenwei01/wt_melting_runtime_20260724/conda_prefix/bin/prterun \
  --readelf /usr/bin/readelf \
  --chrpath /usr/bin/chrpath \
  --strace /usr/bin/strace \
  --unshare /usr/bin/unshare \
  --mount /usr/bin/mount \
  --bash /bin/bash \
  --python /usr/bin/python3
```

先行接收标准为：

1. 私有 user/mount namespace 建立成功，外部旧运行时未变化；
2. 4-rank handshake、严格 exec 集合、maps、22 个旧前缀失败探针均通过；
3. recovery mapped component counterpart 检查全部可证明；
4. 074 计算收敛，`|dE| < 0.1 meV/atom`、`|dP| < 0.02 GPa`，审计退出 0；
5. namespace payload 作为 PID 1 正常退出，`unshare --pid --fork --kill-child=KILL`
   的实际命令、exit 0 和 inode 一致性形成 kernel reap 证明；host 可访问 inode 扫描未见
   同 inode 成员；strace、handshake、descendant 和 PGID 汇总的每个已知 PID 都有消失
   或 PID-reuse 终态证据；
6. 命令、脚本 SHA、stdout/stderr、`audit.json`、`objects.tsv`、counterpart、namespace、
   strace 和结果摘要归档到受管证据目录并生成校验和。

`evidence_manifest.tsv` 完整枚举 run 树每个 leaf 的相对路径、Git mode、字节数和
SHA-256；新增、删除、类型/mode 或 blob 变化均拒绝。`summary.json` 冻结实际执行的
`code_commit`、runtime/tool/wrapper/ELF 身份和完整实现闭包，但不伪造尚不存在的
`smoke_commit`。首次成功先写完 manifest 与 summary，完成严格本地证据、原始 trace/maps、
namespace、输入归档和科学量的 **precommit validation**，入口返回
`status=pending_commit` 和下一条提交命令。此时必须执行：

```bash
git add analysis/s1/runtime_relocation_smoke_20260805
git commit -m "record managed 074 runtime-relocation smoke"
```

随后以完全相同参数再次调用上述 smoke 入口；它不得重跑 074，而应幂等执行 committed
validation 并返回 `status=accepted_committed`。committed validator 从当前 canonical
完整树每个 leaf 的**最新** introduction 推导唯一 `smoke_commit`，要求所有 leaf 同次
提交、当前 blob/mode/type 与该提交一致，并要求 `smoke_commit^ == code_commit`。正式
generator/validator 只接受这一 committed 结果；未提交的 `pending_commit` 绝不能生成
正式 config/manifest。

失败 smoke 不删除、不原地覆盖。先提交完整失败根；下次专用入口自动移动到
`failed_runs/runtime_relocation_smoke/attempt-<failure-commit-prefix>/` 并单独提交，随后
以同一 smoke ID 创建新 attempt。未提交或缺 machine-readable rejected status 的失败根
拒绝自动归档。若执行及科学量已通过、但 precommit validation 拒绝，入口必须把 optimistic
summary/manifest 原样移动为 `run/precommit_candidate_*`，并原子写出 rejected replay、
`failure.json` 和完整 validation error，不能留下表面 accepted 根；已提交 accepted 根只
幂等验证。所有历史 failed-smoke archive 都是正式硬门：archive commit 的
parent 必须等于目录名登记的 failure commit，failure/copy/current HEAD 的完整 leaf 集合、
mode、type、blob 必须一致；任何历史 archive 的删除、增加、篡改或工作树漂移都使
generator 和 validator 拒绝。

074 smoke 只证明执行通路具备条件，不替代正式 preregistration 或六点结果。

## 3. 正式冻结

前置硬门：S1-R8 42/42 分析为 `accepted`；六个引用均收敛且原始日志重解析与
`result.json` 完全一致；源文件、引用证据和算法闭包均已由当前 HEAD 跟踪；工作树
干净；074 先行检查已接收。

真实 canonical CLI 为：

```bash
scripts/generate_s1_runtime_relocation_equivalence.py \
  --recovery-prefix /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix \
  --old-prefix /home/shenwei01/wt_melting_runtime_20260724/conda_prefix \
  --abacus /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/source/abacus_pw_para_relocated_20260805 \
  --mpirun /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix/bin/mpirun \
  --reference-mpirun /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix/bin/mpirun \
  --launcher /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix/bin/prterun \
  --reference-launcher /home/shenwei01/wt_melting_runtime_20260724/conda_prefix/bin/prterun \
  --readelf /usr/bin/readelf \
  --chrpath /usr/bin/chrpath \
  --strace /usr/bin/strace \
  --unshare /usr/bin/unshare \
  --mount /usr/bin/mount \
  --bash /bin/bash \
  --python /usr/bin/python3 \
  --smoke-summary analysis/s1/runtime_relocation_smoke_20260805/summary.json

scripts/validate_s1_runtime_relocation_equivalence.py \
  config/S1_runtime_relocation_equivalence_manifest.tsv \
  --config config/S1_runtime_relocation_equivalence.json

git add config/S1_runtime_relocation_equivalence.json \
  config/S1_runtime_relocation_equivalence_manifest.tsv
git commit -m "preregister S1-R8 runtime-relocation equivalence replay"
```

生成器缺任何引用或硬门失败时必须非零退出，两个正式输出都不得出现；已存在的正式
输出绝不覆盖。正式运行前必须再执行带 `--require-committed` 的 validator。

## 4. 隔离、环境和确定性启动

每点以非 root host 用户从干净工作树启动。外层固定使用：

```text
/usr/bin/unshare --user --map-root-user --kill-child=KILL \
  --mount --pid --fork --mount-proc --propagation private \
  /bin/bash <namespace-payload>
```

namespace 内以 `size=1m,nosuid,nodev,noexec` 的 tmpfs 覆盖旧 runtime root；host 上
旧 root/prefix 的 lstat、realpath 和 mountinfo 在前后必须完全相同。证据同时冻结
原始 mountinfo、uid_map、gid_map、PID namespace inode/NSpid、namespace init PID 1、
无 shared propagation、payload 状态、工具前后身份和清理后的零残留进程。外层退出后
权威终态证明要求：实际 `unshare` 命令与冻结 argv 完全一致，payload Bash 在三份状态
证据中均为同一 PID namespace 的 PID 1，`process.wait()` 正常返回，payload/unshare 均
exit 0。Linux PID namespace 规则规定 PID 1 终止时 kernel 对 namespace 中其余进程发送
`SIGKILL`；该不变量与 `--kill-child=KILL`、外层 PG/descendant 零残留共同构成
`pid1_kernel_reap_proof`。参见
[pid_namespaces(7)](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)。

host `/proc/*/ns/pid` 扫描保留为辅助负向硬门：任何**可访问**的同 inode 成员立即拒绝；
`/proc` 无法列举、目标 namespace inode 无效等审计自身错误也拒绝。节点因 hidepid/权限
策略不可读的无关 PID 必须记录数量和有限样例，但不得伪称扫描穷尽，也不覆盖 PID 1/kernel
证明。不得把默认 `true`、一次 PGID 快照或仅“未发现可读成员”当成零残留证明。

内层 7200 s、外层 7260 s 都是覆盖 preflight、工具 `--version`、计算、逐文件哈希、
counterpart、postflight 和 summary 写入的绝对 deadline。每个阻塞子进程使用剩余时间
timeout，逐块哈希前后复核 deadline，两层另有 process-wide watchdog。summary 记录
timezone-aware UTC、start/end epoch 和 monotonic elapsed；validator 重算一致性，超时或
elapsed 越界一律拒绝。唯一 start/deadline 在各 launcher 的 `main()` 审计入口建立并传入
全部实现；SIGALRM 使用不会被 `OSError`/`TimeoutError`/宽泛 `Exception` 捕获吞掉的专用
异常。alarm 触发后即使执行清理和零残留复核，最终也只能写出 rejected/124，不能恢复为
accepted。

运行环境从 `env -i` 建立，并要求：

- `OPAL_PREFIX`、`PRTE_PREFIX`、`PMIX_PREFIX`、`UCX_MODULE_DIR` 全部精确等于
  recovery prefix；
- `LD_LIBRARY_PATH=<recovery_prefix>/lib`，`LD_PRELOAD` 不存在；
- `PATH=<recovery_prefix>/bin:/usr/bin:/bin`；
- `CMAKE_PREFIX_PATH` 和 `MKLROOT` 精确等于 recovery prefix；
- `OMP_NUM_THREADS=1`；
- `CUDA_CACHE_DISABLE=1`；`HOME=runs/<ID>/runtime_home` 初始为 `0500`，并在 private
  mount namespace 中 self-bind 后以 `ro,nosuid,nodev,noexec` 重挂载；before/after raw
  mountinfo 必须证明唯一的只读 HOME mount。目录中只能有登记的 `CONTROLLED_HOME.txt`，
  既不得读取用户 `.openmpi` 等配置，也不得写入默认 `~/.nv/ComputeCache`；
- `environment/activate.sh` 属于冻结的实现闭包。

runner 使用独立 FD 9 读取清单；子任务关闭 FD 9，stdin 接 `/dev/null`。每个 rank
先运行冻结的 Python rank wrapper，写出原子 ready JSON 后等待逐 rank release。审计
器读取并复核恰好 4 个 ready（0–3）、PID、target ABACUS、四个 prefix 和最终环境，
再依次 release、`SIGSTOP`、读取 `/proc/<pid>/maps`、`SIGCONT`。不得用 20 ms 抽样式
PID 猜测替代 handshake。PRRTE 启动 rank 时可能清空 `PRTE_PREFIX` 并重复前置同一个
recovery `LD_LIBRARY_PATH`；rank wrapper 只在所有非空分量均精确属于 recovery 时，
把四个 prefix 与库路径收敛为冻结的唯一值，任何外来分量仍硬拒绝。
release token 必须由审计器以临时文件、`fsync` 和原子发布生成，且内容精确为
`release\n`；rank 在 exec 前必须用 no-follow FD 读取普通文件，并在读取前后
均确认 abort 不存在。空、部分、`abort\n` 或符号链接 token 均不得执行 ABACUS。

`/usr/bin/strace` 固定使用与服务器 5.16 兼容的
`-ff -qq -I 1 -s 4096 -e trace=file,process`，且命令前缀、trace 输出前缀和后续 MPI
命令必须逐项完全一致；不得登记该版本不支持的 `--kill-on-exit`。其 path、realpath、
SHA-256、版本输出必须在执行前后完全一致。tracee 收口不依赖该缺失选项，而由审计器
进程组与跨 channel known-PID 终态证明、`unshare --kill-child=KILL` 私有 PID namespace
的 PID 1/kernel reap 证明、可访问 host inode 负向扫描共同完成；任一层不完整均拒绝。执行
链的成功 `execve` 必须是精确 multiset：mpirun 1、recovery prterun 1、冻结 Python
4、relocated ABACUS 4。只有明确 `result == 0` 才算成功；额外成功 exec 或截断/未知
result 均拒绝。launcher PID 必须直接来自 raw strace 中唯一一条成功执行
冻结 launcher realpath 的 `execve` 记录；`/proc/<pid>/exe` 只用于证明该权威 PID
在 maps 捕获时仍存活。PRTE 的 `CLONE_THREAD` 线程可共享同一 executable，因而
额外 `/proc/exe` 候选只记录为诊断证据，不计为额外 launcher。进程结束后必须
从完整 trace 重算一次，记录数或 PID 变化均拒绝。

审计器从 strace trace 文件、clone/exec 过程证据、rank handshake、动态 descendant 和
PGID 扫描合并已知 PID 集合，并逐 PID 记录 observed start-time 与终态 `gone` 或
`pid_reused_original_gone`；任何 `still_present_or_identity_unproven` 都拒绝。PID
validator 只接受 regular、非符号链接且名称严格为 `trace.<positive-int>` 的 raw trace；
launcher/rank handshake PID 必须逐一出现在 raw trace 中，并与 maps process rows 的
launcher PID 和 `{rank: PID}` 精确相等。known-PID 集还必须与全部 trace PID、cleanup
PG/descendant 逐一交叉覆盖，并要求 strace PG leader 带 `strace_root` 来源。
launcher 的终态行必须同时带 `launcher_strace_exec` 权威来源和
`launcher_proc_exe_live` 活性来源；maps、handshake 与终态行的 `/proc` start-time tick
必须一致，防止 PID 复用或中途身份替换。
PID 1/kernel reap 证明和可访问 host inode 负向扫描是外层独立硬门；连同内层 known-PID
与 PG 零残留全部通过，才可声称本次执行无残留 tracee。

## 5. maps 与 recovery↔old counterpart 硬门

maps 的可证明观察范围明确收窄为最终 recovery prterun 和 4 个 ABACUS rank；不声称
覆盖已 exec 消失的 mpirun 阶段或 support-daemon maps。该范围内每个 regular mapped
object 都必须记录 mapped path、realpath 和 SHA-256。

允许分类仅包括：

- recovery runtime；
- system roots `/usr`、`/lib`、`/lib64`；
- 精确路径 `/etc/ld.so.cache`；
- 登记的 InfiniBand/NVIDIA device 形态；
- 严格的 `/SYSV...`、`/dev/shm/sm_segment...`、
  `/dev/shm/ucx_shm_posix_...`、有限 PMIx/CUDA `/tmp/ompi.<pid>/...`，以及
  `^/tmp/ompi\.[0-9]+/hwloc\.sm$` transient 形态。

不得把整个 `/dev`、`/proc`、`/sys` 或 `/tmp` 作为 allowlist。旧前缀 maps 和未知
maps 都必须为 0。

每个捕获到的 recovery component 还必须按相对路径定位旧 runtime root 的
counterpart，记录 recovery path/realpath/SHA 与 old path/realpath/SHA，并要求字节
相同。只有三类显式规则可排除普通 counterpart：relocated ABACUS 由 ELF byte gate
证明；mpirun 和 prterun 由各自的 reference/replay identity gate 证明，后二者仍要求
字节相同。counterpart 缺失、SHA 不同或覆盖不全即
`mapped_component_byte_equivalence_unprovable`，六点不得接收。

## 6. 旧前缀访问：精确 22 个 ENOENT

旧前缀成功访问数、成功 exec 数和未知失败探针数必须都为 0。不是“零尝试”；每点只
允许下列精确 22 个 `ENOENT`，且 role、rank、syscall、flags、errno 和 count 全匹配：

| 角色 | 路径/调用 | 数量 |
|---|---|---:|
| launcher | `stat(<old>/classid)` | 1 |
| launcher | `openat(<old>/classid, O_RDONLY\|O_CLOEXEC)` | 1 |
| 每个 rank | 同上两种 classid 调用各 1 | 8 |
| 每个 rank | `openat(<old>/ucx.conf, O_RDONLY)` | 4 |
| 每个 rank | `openat(<old>, O_RDONLY)` | 4 |
| 每个 rank | `openat(<old>, O_RDONLY\|O_NONBLOCK\|O_CLOEXEC\|O_DIRECTORY)` | 4 |

classid 合计 10，ucx.conf 4，旧 prefix directory 8，总计 22。任何成功旧访问、旧
exec、额外失败探针或计数不符均拒绝。

## 7. 顺序执行、失败保全和同 ID 重试

默认正式执行命令为：

```bash
scripts/run_s1_runtime_relocation_equivalence.sh
```

也可严格按 CLI 传入 `[MANIFEST_TSV [CONFIG_JSON]]`。runner 每次只执行一个点，先做
core validation，再写 schema-2 状态，字段分别保存 `workflow_exit_code`、
`invocation_exit_code`、`launcher_exit_code`、`parser_exit_code`、
`core_validation_exit_code`、audit/namespace/counterpart 状态，不得把 parser-only 或
validation-only 失败误记为 launcher 失败。

创建 attempt 目录后立即安装 EXIT trap；setup、metadata、launcher 或 parser 的早期
shell 失败都必须原子写出 machine-readable `run_status.json`、`replay_status.json`、
`failure.json`。`setup_completed` 与 `failure_stage` 明确区分执行前失败；外层即使发现
缺失/损坏的 `run_status.json` 也从捕获的退出码合成严格失败证据，不得留下无法续跑的
脏目录。

成功点验证后单独提交。失败点同样解析可用日志、重算 raw strace、写
`run_status.json`、`replay_status.json`、`failure.json`，验证后单独提交并立即停止。
下次续跑必须先把已提交失败移动到：

```text
failed_runs/runtime_relocation/<ID>/attempt-<failure-commit-prefix>/
```

再单独提交 archive，之后才允许用同一登记 ID 重试。严格校验使用当前
`runs/<ID>` 的最近 introduction commit，并分别验证所有历史 failed archive 的完整
Git leaf 集合：relative path、mode/type 和 object id 必须与 failure commit 的
`runs/<ID>` 完全一致，当前 archive 工作树也必须等于 HEAD。任何删除、新增、符号链接/
可执行位/gitlink 类型变化或 blob 变化均拒绝；同时验证 commit-parent 链。因此
fail→commit→archive→same-ID retry 是可验证流程。不得删除失败证据或原地覆盖。

## 8. 科学硬门和处置

六点完成后运行：

```bash
scripts/analyze_s1_runtime_relocation_equivalence.py \
  analysis/s1/runtime_relocation_equivalence_20260805 \
  --config config/S1_runtime_relocation_equivalence.json \
  --manifest config/S1_runtime_relocation_equivalence_manifest.tsv
```

分析器重新解析 reference/replay 原始 `running_scf.log`，不信任已存数值。OF 使用
`!FINAL_ETOT_IS`；KS 使用 `E_KS(sigma->0)` entropy-corrected estimator。每点必须
同时满足：

- `|dE| < 0.1 meV/atom`；
- `|dP| < 0.02 GPa`；
- 全部 ELF、exec、namespace、环境、strace、maps、counterpart 和旧前缀访问门通过；
- 用 replay 替换对应 R8 曲线 v100 点后，该系列状态与 R8 全局状态保持原来的
  `accepted`，六项均不翻转。

`storage_exact`、`storage_resolution_equal`、`scientific_tolerance_only` 仅作数值存储
分辨率诊断。只要上述科学阈值、全部 runtime gates 和 R8 replacement 6/6 均通过，
协议直接关闭；没有未预注册的 endpoint expansion 分支，不得事后选择 EOS 端点。

以下任一项触发完整 42 点在 recovery runtime 下重跑：

1. ELF 在登记 RUNPATH 槽外不同，或 `NEEDED`、Build ID、LOAD 布局改变；
2. 任一点 `|dE|`/`|dP|` 科学硬门失败；
3. R8 series/fit hard gate 或替换后的六点结论改变；
4. mapped component counterpart 字节等价无法证明；
5. 任何不能归类为纯审计操作故障的实质 runtime gate 失败。

只有能够证明是 namespace launcher、审计采集或工具操作本身的故障，修复后才允许仅
重试六个登记点；它不能掩盖旧路径成功访问、额外 exec、未知 map 或 counterpart
不一致。

## 9. 名称兼容与交接

canonical 对外入口是：

- `run_s1_runtime_relocation_smoke.py`
- `generate_s1_runtime_relocation_equivalence.py`
- `validate_s1_runtime_relocation_equivalence.py`
- `run_s1_runtime_relocation_equivalence.sh`
- `analyze_s1_runtime_relocation_equivalence.py`
- `s1_runtime_relocation_equivalence_common.py`

历史 `*_mpi_prefix_equivalence*` 名称只作为严格兼容别名/转发，调用同一 canonical
runtime-relocation 语义，不提供旧的宽松协议，也不得生成旧 config 名。Python 内部
实现文件暂沿用部分历史模块名不改变这一对外约束。

交接时依次检查：工作树干净；074 受管 smoke 证据；canonical config/manifest 是否
已由生成器创建并提交；最后一个已提交/已归档的 113–118；逐点 status 与审计原始
证据；最终 `summary.json`、`points.tsv` 和 README。正式 config 不存在就表示仍处于
先行检查/冻结前阶段，禁止启动 113–118。
