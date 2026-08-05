# S1-G1-ELECTRON-NUMBER-R2 增量电子数积分协议

本文件是不可变的 R2 规范。R2 不改写、不追溯改判 R1 证据，只修正
whole-runtime audit 对 LLVM OpenMP 短命共享内存对象的分类。最终 R2
报告合并 11 个 R1 accepted OF 点和 19 个 R2 新执行 OF 点，仍只关闭
G1 的“独立电子数积分”子项；其余五个 G1 子项与完整 G1 门保持
`pending`。

## 1. R1 事实与修订理由

- R1 预注册提交为 `f3efec315b1074c34709f8040f978d72575b6f10`；
- S1-119–129 已逐点严格验证并提交，共 11 点；
- S1-130 求解收敛，且独立积分和科学等价性均通过，但 runtime
  sampler 捕获了 rank 1 的
  `/dev/shm/__KMP_REGISTERED_LIB_21_0`，R1 将其误分为 `unexpected`；
- S1-113–129 和归档的 S1-130 原始 strace 每次都显示 4 个 rank
  各自完成一次 create、read-open 和 unlink，而 map sampler 是否在短暂
  存活窗口捕获对象取决于采样时序；
- S1-130 失败提交
  `a894c735d95c3fc8d74f3cdb7fb8b16d1fd2c075` 已由相邻提交
  `8eb9231cd4500c4bb2d6a4d84aa822ba234374d8` 完整归档。

因此禁止通过重试等待 sampler “碰巧看不见”该对象。R2 必须从 raw
strace 验证完整生命周期，同时仍保持 `unexpected_mapped_object_count=0`。

## 2. 不可变的 R1 边界

R2 不得修改以下任何对象：

1. R1 protocol、config、manifest 与 `inputs/s1/electron_number_audit/`；
2. R1 implementation closure 中的所有路径，特别是
   `runtime_relocation_audit_launcher.py`、
   `s1_mpi_prefix_equivalence_common.py` 和
   `validate_s1_mpi_prefix_equivalence.py`；
3. S1-113–118 的共享 runtime-relocation R2 config、manifest、summary 和 run
   trees；
4. S1-119–129 accepted run trees 及其 `electron_number_audit.json`；
5. S1-127 和 S1-130 的全部 failed-attempt archives。

R2 generator 和最终 validator 必须用原 R1 validator 逐点重验
S1-119–129，并验证 S1-127/130 归档链。R1 不完整的
`--require-all-runs` 不作为 R2 前置条件；R2 只复用已被 R1 逐点接受的
11 个事实。

## 3. R2 范围、ID 与输入

- 总验收分母仍为 90：60 个已有 KS 密度和 30 个 OF 高精度 cube；
- R1 复用集合固定为 S1-119–129，共 11 点；
- R2 执行集合固定为 S1-130–148，共 19 点；
- S1-130 的 R1 失败尝试已归档，R2 使用同 ID 的新 introduction；
- S1-131–148 在 R2 预注册时必须从未 introduction；
- 不分配新 ID，不重跑 S1-119–129，也不得归档或覆盖 accepted
  run tree。

R2 复用 R1 已预注册的
`inputs/s1/electron_number_audit/S1-20260805-130`–`148`。这些输入包的
`metadata.json` 仍记录 R1 输入注册来源；R2 证据必须另行记录 R2 执行与
runtime profile。禁止新建 R2 input root，因为这会无必要地改变已冻结的
input-directory、suffix 和 metadata 哈希。

R2 continuation manifest 仅含 S1-130–148 的 19 行，表头和每个字段必须
与 R1 manifest 对应行完全一致。R2 preregistration commit 只得引入：

- `config/S1_electron_number_audit_r2.json`；
- `config/S1_electron_number_audit_r2_manifest.tsv`。

两个路径都必须在 S1-130–148 的 R2 run introduction 之前提交。对
S1-130，“之前”指早于归档后的**最新** run introduction；旧 R1 失败
introduction 只能位于已验证的 archive chain 中。

## 4. Runtime profile 的唯一修订

R2 `runtime` 从 R1 electron-audit config 逐字段复制，只允许：

1. `runtime.wrappers.audit_launcher.path` 指向
   `scripts/runtime_relocation_audit_launcher_g1_r2.py` 的 node01 绝对路径；
2. 同一 wrapper 的 `sha256` 改为新 shim 哈希。

R2 `runtime_audit` 从 R1 逐字段复制，只在
`transient_mapping_patterns` 末尾追加：

```text
^/dev/shm/__KMP_REGISTERED_LIB_[1-9][0-9]*_0$
```

不得改变旧前缀、namespace、executable、counterpart、hash、timeout 或科学门；
`old_prefix_mapped_object_count_max`、`unexpected_mapped_object_count_max` 和
`unhashed_regular_mapped_object_count` 的验收值仍为 0。

## 5. KMP 生命周期硬门

单靠路径正则不足以放行映射。R2 必须从原始 `trace.*` 和
`objects.tsv` 独立证明每次 run：

- 恰有 4 个 rank KMP lifecycle，每个 rank 恰有 3 个成功 syscall，总数
  恰为 12；
- 对象名第一个整数与该 rank 的 trace PID 相同，UID 后缀严格为
  user/PID namespace 内的 `0`；
- 同一 PID 按顺序完成一次带
  `O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC` 的成功 create，一次带
  `O_RDONLY|O_NOFOLLOW|O_CLOEXEC` 的成功 read-open，以及一次成功
  `unlink`；
- 只允许 `rank` 角色产生该对象；
- 每个 rank 必须同时映射冻结 recovery prefix 中的 `libomp.so`，其
  path、realpath 和 SHA-256 全部与 config 一致；
- sampler 可捕获 0–4 个该短命映射；凡被捕获者都必须分类为
  `transient_system`，任何近似名称、PID/UID 不匹配或生命周期不完整者仍为
  `unexpected` 并拒绝。

R2 generator 必须使用同一冻结 contract 重验 S1-119–129，并将
libomp 身份写入 config。归档 S1-130 只用于证明根因，不可事后
改写或计入 accepted 分母。

## 6. 执行顺序与双 pilot

固定执行顺序是：

```text
130, 135, 131, 132, 133, 134, 136, 137, 138, 139, 140, 141, 142, 143,
144, 145, 146, 147, 148
```

S1-130（Mg，source 052）与 S1-135（Al，source 071）是 R2 runtime profile
的两个 pilot。在两者都经 committed R2 validator 验收前，其余 17 个
run prefix 必须不存在，也不得有 introduction commit。runner 必须从 config
的 `execution_order` 驱动，不得依赖 manifest 行顺序或 shell glob。

每点仍执行：运行→runtime/KMP/scientific/electron core validation→写 R2 evidence→
完整验证→单点提交。任何失败必须先将失败 run 和机器可读状态提交，
再用相邻提交归档到唯一 `attempt-<failure_commit[:12]>`；若 R2 contract
本身需要扩展，必须启动新修订和新预注册，不得修改已预注册 R2。

## 7. 可量化验收标准

- [ ] R2 implementation commit 只新增版本化文件，R1 implementation 哈希变化
  0；
- [ ] generator 从 node01 干净工作树执行，且执行前 R2 config/manifest
  均不存在；
- [ ] generator 时 S1-130–148 的 active run directories 存在数为 0；
- [ ] R1 registration 严格通过，S1-119–129 逐点 accepted validator 通过
  11/11，KMP lifecycle 通过 44/44 ranks；
- [ ] S1-127 和 S1-130 failed archive chains 通过，缺失或树变化为 0；
- [ ] R2 continuation manifest 恰有 19 行，与 R1 对应行逐字段不同数为
  0；
- [ ] R2 preregistration commit 的 changed-path set 恰为 config+manifest 两个路径；
- [ ] runtime 结构差异恰为 shim path/hash 两个 leaf，runtime-audit 结构差异
  恰为追加一条 KMP regex；
- [ ] S1-130/135 pilot accepted 2/2 后才展开其余 17 点；
- [ ] R2 新执行 accepted 19/19，R1 复用 accepted 11/11，OF 合计
  30/30；
- [ ] KS 60/60，总覆盖 90/90，缺失 0，失败 0；
- [ ] 每点 `certified_relative_error < 1e-10`；
- [ ] 30/30 OF 严格满足 `|delta E| < 0.1 meV/atom` 与
  `|delta P| < 0.02 GPa`；
- [ ] 30 个 accepted OF runs 中 KMP lifecycle 通过 120/120 ranks，成功生命
  周期 syscall 恰为 360/360；
- [ ] old-prefix successful access/mapping、unexpected mapping、unhashed regular mapping
  全部为 0；
- [ ] 最终 summary 明确报告 `R1 reused=11`、`R2 executed=19`，且旧
  S1-130 失败尝试只出现在 archive/root-cause provenance 中。

## 8. 完整重跑的触发条件

只有以下情况才放弃增量 R2，新分配 S1-149–178 并重跑 30 点：

1. 任一 S1-119–129 无法由原 R1 validator 重验；
2. 任一复用点缺少符合本协议的 raw KMP lifecycle；
3. R2 改动了 ABACUS、MPI、libomp、namespace、科学输入、积分算法或
   验收阈值；
4. 治理规则明文要求 30 点必须由同一 runtime-audit revision 现场执行。

在当前证据下，上述条件均不成立，所以增量 11+19 是最小且科学上充分的
修订。
