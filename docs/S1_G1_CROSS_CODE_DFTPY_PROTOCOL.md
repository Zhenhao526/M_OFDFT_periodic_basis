# S1/G1 独立 OFDFT 跨代码 EOS/压力复核协议（R2）

协议版本：`S1-G1-CROSS-CODE-DFTPY-20260810-R2`
正式分母：14 个新 DFTpy 计算，Al、Mg 各 7 个体积点。  
目标：关闭 G1 六项中的“第二个独立 OFDFT 实现”一项；本协议不代替 `KS-NL → KS-L → OF-L` 三层物理误差验证。

## 1. 比较对象

- 参考实现：仓库中已经收敛的 ABACUS OFDFT 高截断七点 EOS，Al 为 `S1-20260805-071..077`，Mg 为 `S1-20260805-092..098`。
- 独立实现：DFTpy 2.2.0；PBE 与 LDA-PZ 由 pylibxc/LibXC 7.0.0 提供。
- 体积比固定为 `0.90, 0.94, 0.97, 1.00, 1.03, 1.06, 1.10`。三点只能做覆盖检查，不能稳定拟合四参数 BM3，因此正式验收使用完整七点曲线。
- 两实现逐点使用同一 `STRU`、同一局域赝势文件字节、同一 XC、WT `alpha=beta=5/6`、自动单元平均 `rho0`，并让 DFTpy 使用 ABACUS 日志记录的同一实空间电荷网格尺寸。
- Al 使用 PBE 和 `assets/pseudo/al.gga.psp`；Mg 沿用已验收的 legacy LDA-PZ 基线和 `assets/pseudo/mg.lda.lps`。Mg 结果只关闭该 legacy 基线的跨实现一致性，不能被表述为统一 PBE 主线已经完成。

机器可读分母位于 `config/S1_g1_cross_code_dftpy_manifest.tsv`；软件、源码、依赖和赝势哈希位于 `environment/dftpy_2.2.0_pylibxc_7.0.0.lock.json`。

## 2. 运行与证据

正式运行必须晚于本协议、config、manifest、runner 和环境锁的预注册提交。2026-08-10 在 Al/Mg `V/V0=1.00` 上做过两个兼容性 smoke；它们没有正式 ID、不进入 14 点分母，也不得复制为正式证据。

R1 的 `DFTPY-S1-20260810-001..014` 已原样保留但判为 rejected：科学量通过 EOS/V0/压力门，然而 DFTpy 的全局 `STDOUT` 句柄没有随 Python `redirect_stdout` 更新，导致逐点 `run.stdout` 为空；同时独立密度重积分错误地使用 `0.529177210903 Å/Bohr`，而保存的 DFTpy 密度坐标使用 ASE/DFTpy 的 `0.5291772105638411 Å/Bohr`，造成约 `10^-9` 的假电子数偏差。R2 使用全新的 `015..028` ID，R1 ID 永不重试，并把 DFTpy 内部 `cell_volume_bohr3` 写入结果后独立积分。

正式运行的每点证据至少包括：

- `result.json`：总能、逐项能量、应力、压力、电子数、收敛码、主机/Slurm 身份、软件版本和输入 SHA-256；
- `density.npy`：以 `electron/Bohr^3` 保存的 float64 实空间密度；
- `run.stdout`：非空的 DFTpy 密度优化原始输出，且必须包含 `Density Optimization Converged`；
- 独立分析产生的 `points.tsv`、`summary.json` 和 `README.md`。

任何失败或缺失点保留在固定分母中，不得换 ID 或删除后重算。正式输出先写到仓库外的
`/home/shenwei01/.local/state/m_ofdft/s1_g1_cross_code_dftpy_r2_20260810`，完成后原样收集到分析证据目录；不得覆盖既有 ABACUS 运行。

运行使用单 CPU 进程，并固定 `OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=1`。DFTpy 优化器固定为 TN，`econv=1e-8 Hartree/atom`、`maxfun=50`、`maxiter=200`，且 `Optimization.converged` 必须为 `0`。

## 3. 量化接收标准

所有不等式均逐材料判定，且 Al、Mg 必须同时通过：

1. 14/14 正式点成功、无缺失、DFTpy 收敛码均为 0。
2. 从保存的 `density.npy`、单元体积和网格点数独立重积分，所有点 `|Ngrid-Nvalence| <= 1e-10`。
3. 两条 EOS 分别以各自 `V/V0=1.00` 点为零点；七个相对能差的 RMS `<= 2 meV/atom`。
4. 对两实现各自的完整七点能量做同一 BM3 拟合；`|V0_DFTpy/V0_ABACUS-1|*100 <= 0.2%`。
5. 压力定义为 DFTpy 应力的 `-trace(stress)/3`；七点中的最大绝对压力差 `<= 0.05 GPa`。
6. 绝对总能不得先平移再声称一致。报告必须列出逐点 `E_DFTpy-E_ABACUS`、其材料内均值和去均值 RMS，并明确审计：两代码读取的是同一 LPP 字节；局域势 `G=0`、Hartree `G=0`、离子 Ewald/自能的分项约定可能改变绝对常数；只有第 3 条相对 EOS 门允许消去一个材料内常数。

任一项不通过时，该子项状态为 rejected；可以保留结果作误差画像，但 G1 不增加完成项。

## 4. 执行入口

在预注册提交之后，以锁定 Python 执行：

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
/home/shenwei01/.local/venvs/m_ofdft-dftpy-2.2.0-py311/bin/python \
  scripts/run_s1_g1_cross_code_dftpy.py \
  config/S1_g1_cross_code_dftpy_manifest.tsv \
  --config config/S1_g1_cross_code_dftpy.json
```

随后运行 analyzer 和 validator；只有 validator 在干净且已提交证据树上返回 0，才能把项目进度从 G1 `2/6` 更新为 `3/6`。
