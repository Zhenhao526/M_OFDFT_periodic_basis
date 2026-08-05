# S1-R8-MPI-R1 六点前缀等价复核协议

状态：`generator_ready_not_frozen`。当前提交没有伪造
`config/S1_mpi_prefix_equivalence.json` 或
`config/S1_mpi_prefix_equivalence_manifest.tsv`；只有在 S1-R8 的 42 点分析已验收、
且 074/081/088/095/102/109 六个引用结果完整、收敛并已提交后，生成器才会写出这
两个正式文件。

## 固定矩阵

| 新实验 | 引用实验 | 材料 | R8 系列 |
|---|---|---|---|
| S1-20260805-113 | S1-20260805-074 | Al | OFDFT next cutoff, v100 |
| S1-20260805-114 | S1-20260805-081 | Al | KSDFT next cutoff, v100 |
| S1-20260805-115 | S1-20260805-088 | Al | KSDFT next kmesh, v100 |
| S1-20260805-116 | S1-20260805-095 | Mg | OFDFT next cutoff, v100 |
| S1-20260805-117 | S1-20260805-102 | Mg | KSDFT next cutoff, v100 |
| S1-20260805-118 | S1-20260805-109 | Mg | KSDFT next kmesh, v100 |

不得重新生成或修改输入。每个新实验逐字复用其 R8 引用行的 `input_directory`；
生成器和校验器固定并复核 `INPUT`、`STRU`、`KPT`、`metadata.json`、赝势、引用
`result.json`、引用原始日志、引用运行元数据、ABACUS 和 mpirun 的 SHA-256。
由于恢复版 `mpirun` 会继续 `exec` 同一前缀的 `prterun`，正式配置还分别冻结最终
launcher 的 realpath 和 SHA-256；二者不得混写成同一个进程证据。

## 第一步：引用就绪后正式冻结

量化接收标准：R8 summary 为 `accepted`；六个引用均收敛；原始日志重解析与
`result.json` 完全一致；全部源文件和引用证据已被当前 Git HEAD 跟踪且工作树干净；
恢复前缀中的 mpirun 和恢复根目录中的 ABACUS 均存在且可执行。

```bash
scripts/generate_s1_mpi_prefix_equivalence.py \
  --recovery-prefix /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix \
  --old-prefix /home/shenwei01/wt_melting_runtime_20260724/conda_prefix \
  --abacus /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/source/abacus_pw_para \
  --mpirun /home/shenwei01/M_OFDFT_recovery_S0_20260805_001/conda_prefix/bin/mpirun

scripts/validate_s1_mpi_prefix_equivalence.py \
  config/S1_mpi_prefix_equivalence_manifest.tsv \
  --config config/S1_mpi_prefix_equivalence.json
git add config/S1_mpi_prefix_equivalence.json \
  config/S1_mpi_prefix_equivalence_manifest.tsv
git commit -m "preregister S1-R8 MPI-prefix equivalence replay"
```

若任何引用缺失，生成器必须非零退出，而且两个正式输出都不得出现。正式文件一旦
生成不允许覆盖；参数变化必须升级协议和实验 ID。

## 第二步：顺序执行并逐点提交

量化接收标准：执行前工作树干净；4 ranks；`OPAL_PREFIX`、`PRTE_PREFIX`、
`PMIX_PREFIX` 三者都精确等于恢复前缀；每一点完成后先校验归档和审计，再单独
Git commit；失败点证据也保留并停止后续运行。

单点在 `env -i` 的最小环境中启动；激活脚本最终只设置
`LD_LIBRARY_PATH=<recovery_prefix>/lib`，且 `LD_PRELOAD` 不存在，避免登录 shell
遗留的旧 Conda/MPI component-path 变量污染复核。

```bash
scripts/run_s1_mpi_prefix_equivalence.sh
```

运行器使用独立文件描述符读取清单，并把单点 stdin 接到 `/dev/null`，避免 MPI
吞掉后续清单行。已存在的点只有在其提交归档、原始日志、输入字节和运行时审计全部
通过时才可跳过。

## 第三步：运行时前缀审计

每点必须产生 `runs/<ID>/mpi_runtime_audit/audit.json`、`objects.tsv` 和
`strace/trace*`。硬门如下：

- 观察到 1 个 launcher 和 ranks 0、1、2、3；
- strace 同时观察到冻结的 `mpirun` 调用和冻结的最终 `prterun` exec；
- 旧前缀映射对象数为 0，未知映射对象数为 0；
- 每个进程的 executable、映射对象 realpath 和可读取对象 SHA-256 均归档；
- `/SYSV...`、指定 `/dev/shm` 段及严格形态的 `/tmp/ompi.<pid>/...` PMIx/CUDA
  共享内存单列为 `transient_system` 并保留路径证据，不误算为 loaded object；
- 旧前缀成功文件访问数为 0；
- 允许且只允许每点恰好两次 `<old_prefix>/classid` 的 `ENOENT` 探针；
- 任何其他旧路径尝试、任何成功旧路径访问或任何旧前缀映射均拒绝。

因此报告不得写成“旧前缀访问尝试为 0”。正确口径是“旧前缀成功访问为 0；另有
两次已注册、失败且无数据读取的 classid 探针”。远端已确认 `strace` 可用，正式
运行将其设为必需，而不是可选降级。

## 第四步：原始日志复算和结论不变性

```bash
scripts/analyze_s1_mpi_prefix_equivalence.py \
  analysis/s1/mpi_prefix_equivalence_20260805
```

分析器不信任已有 `result.json` 数值，会重新解析每个引用和 replay 的原始
`running_scf.log`。OF 使用 `!FINAL_ETOT_IS`；KS 使用日志中的
`E_KS(sigma->0)` 字段，并明确标为 entropy-corrected estimator，而非严格零温能。

每对都必须同时满足严格不等式：

- `|dE| < 0.1 meV/atom`；
- `|dP| < 0.02 GPa`；
- 用 replay 替换对应 R8 曲线的 v100 点后，BM3/七点比较的系列状态和 R8 总结论
  均不改变；
- 运行时审计为 `accepted`。

同时报告三个存储层级：`storage_exact`、`storage_resolution_equal`、
`scientific_tolerance_only`。只有前两层的 6/6 才能以六点关闭问题；若 6/6 科学阈值
通过但存在第三层，状态为 `requires_endpoint_expansion`，须追加预注册 EOS 端点。
任一哈希/运行时门失败、任一科学阈值失败、任一 R8 结论翻转或引用/重放不收敛，
处置为在恢复前缀下重跑完整 42 点 R8 矩阵，而不是选择性删点。

## 随时交接检查

接手人依次执行：

1. 读 `docs/M_OFDFT_项目进度与交接.md` 和本文件；
2. `git status --short --branch`，不得在脏工作树启动数值运行；
3. 查看正式 config/manifest 是否存在；不存在表示仍在等待引用齐全，禁止手写哈希；
4. 若已存在，运行严格 validator；
5. 查看 113–118 中最后一个已提交点，并用 runner 安全续跑；
6. 六点完成后运行 analyzer，以 `summary.json`、`points.tsv` 和 README 交接。
