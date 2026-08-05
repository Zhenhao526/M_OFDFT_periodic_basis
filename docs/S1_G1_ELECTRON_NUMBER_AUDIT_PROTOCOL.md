# S1-G1-ELECTRON-NUMBER-R1 独立电子数积分协议

本文件是不可变的 R1 规范；执行状态只记录在正式 `summary.json` 与项目进度文件中。
本协议只关闭 G1 的电子数子项；无论结果如何，
第三展宽/超密 k 标签、第二 OF 程序、KS-NL→KS-L→OF-L、位移/应变参考以及 10/10
单命令重生这五项均不因此豁免。

## 1. 冻结范围

- 主验收集合为 S1-029–070 与 S1-071–112，共 84 个已验收 R7/R8 基准点；
- 补充集合为 S1-113–118 六个 runtime replay；
- 总分母固定为 90，失败或缺密度的点不得从分母删除；
- S1-001–028 属候选扫描/诊断集合，不进入本轮最小 G1 闭环；后续新 G1 密度结果必须
  自动重新进入同一电子数门。

现有 60 个 KS 点从已提交的 `CHARGE-DENSITY.restart` 独立解析。30 个 OF 点没有可用
最终密度，冻结 S1-119–148 一一映射的输出型重演；S1-119/120 分别映射 S1-113/116，
作为 Al/Mg 正式 pilot，二者未全部严格通过前禁止展开其余 28 点。

## 2. 唯一允许的输入变化

OF 重演从源 run 的归档 `INPUT/STRU/KPT` 机械生成。`STRU`、`KPT` 和赝势逐字节不变；
`INPUT` 只允许：

1. `suffix` 改为登记的新输出名；
2. 紧随其后加入 `out_chg 1 17`。

`out_chg` 只控制最终密度输出，不改变科学参数。每个重演仍须收敛，并相对源点同时满足
`|ΔE| < 0.1 meV/atom` 与 `|ΔP| < 0.02 GPa`。

## 3. 独立目标电子数

目标值不得读取 ABACUS 日志或 `result.json` 的名义电子数字段。独立解析 `STRU` 的物种
原子数，并从归档局域赝势 `zatom, zion, pspd` 行读取 `zion`：

\[
N_{\mathrm{target}}=\sum_s n_s z_s.
\]

本集合中 Al 为 1×3=3，Mg 为 2×2=4；manifest 仍逐点冻结推导结果和全部输入哈希。

## 4. 两条独立积分路径

KS reciprocal restart 采用小端二进制的完整记录标记、G 向量和 spin 通道解析。文件必须
无截断、无尾随数据且恰有一个 G=0：

\[
N_{\mathrm{KS}}=\Omega\sum_\sigma \operatorname{Re}\rho_\sigma(G=0).
\]

OF cube 的三维网格必须与本次及源点原始 `running_scf.log` 中唯一的 charge FFT grid
逐项相等，并且必须有且只有 `n_x n_y n_z` 个有限密度值，单位为 e/Bohr³：

\[
N_{\mathrm{OF}}=\frac{\Omega}{n_xn_yn_z}\operatorname{exact\_decimal\_sum}_i\rho_i.
\]

两条路径的晶胞体积均只由
`|det(LATTICE_CONSTANT × LATTICE_VECTORS)|` 得到。cube 头网格步长固定只输出六位
小数，只做 `<1e-4` 相容性诊断，绝不作为高精度体积来源。`STRU` 十进制量、二进制
G=0 双精度值和 cube 科学计数 token 均转为精确有理数；cube 采用公共十进制尺度的整数
求和，并用 `math.fsum` 交叉核对。逐 token 计算打印舍入上界，因此不再用未覆盖解析和
行列式误差的经验 ULP；验收量为
`certified_relative_error=(|Nint-Ntarget|+bound)/Ntarget`。

## 5. 可量化验收标准

- [ ] manifest 在任何 S1-119–148 结果产生前提交，90 个源点和 30 个重演 ID 固定；
- [ ] generator 只在 node01 的正式远端仓库运行；生成前原 R8 committed validator 与
  S1-113–118 runtime committed validator 必须全量通过，R7/R8/R2 accepted summary 的
  哈希与分析提交写入 config；
- [ ] 90/90 密度、结构、赝势、日志与哈希完整，缺失 0、解析失败 0；
- [ ] 每一点 `certified_relative_error < 1e-10`（严格小于）；
- [ ] 30/30 OF 重演收敛，能量/压力严格双门通过；
- [ ] 30/30 使用 node01、4 ranks、登记 relocated ABACUS、recovery MPI 与私有
  user/mount/PID namespace；whole-runtime 严格审计通过；
- [ ] 30 个新 OF 点的证据先本地校验再逐点提交；失败目录及机器可读失败状态先提交、
  再归档到唯一 attempt，原 ID 仅可在归档闭环通过后重试；
- [ ] 最终 `summary.json`、`points.tsv` 与 README 由冻结分析器从已提交证据生成。

通过后只将“G1 电子数独立积分”记为 `accepted`，完整 G1 仍为 `pending`（1/6）。

## 6. 固定执行顺序

1. 提交实现、协议和负向/合成测试；
2. 将实现提交同步到 node01，从远端干净提交运行 generator，提交 config、manifest 与
   30 套派生输入；本地 macOS 不具备冻结的 Linux runtime 路径，不得生成正式登记；
3. 带 `--require-committed` 验证预注册；
4. 顺序执行 S1-119、120；两点均通过 committed validator 后才继续 121–148；
5. 每点运行→独立积分→runtime/科学门复核→单点提交；
6. 30 点完成后生成正式 90 点分析，独立复核后更新进度文档。
