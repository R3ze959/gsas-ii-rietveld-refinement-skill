# GSAS-II Rietveld Refinement Skills

Conservative Codex skills for real GSAS-II powder refinement and separate,
read-only publication plotting. The repository now includes a clearly labelled
beta skill for declared multiphase refinement and quantitative phase analysis.

一个面向真实 GSAS-II 粉末精修的 Codex skill 仓库。精修与画图严格分离，
并包含明确标注为 Beta 的多相精修与定量分析 skill。

## Current release / 当前版本

The current prerelease is **v3.0.0-beta.2**. It retains the
`gsas-ii-multiphase-refinement` beta from beta.1 and upgrades
`rietveld-plotting` with phase-specific Bragg colors, phase-name legends, and
optional covariance-backed crystalline wt% labels. The ordinary
`gsas-ii-rietveld-refinement` skill remains unchanged from v2.2.0. See the
[v3.0.0-beta.2 release notes](RELEASE_NOTES_v3.0.0-beta.2.md).

当前预发布版为 **v3.0.0-beta.2**：保留 beta.1 的
`gsas-ii-multiphase-refinement` Beta，并升级 `rietveld-plotting`，使多相
Bragg 峰按物相使用不同颜色，图例显示物相名，并在存在完整协方差质量分数时
显示晶态物相 wt%。普通 `gsas-ii-rietveld-refinement` 精修 skill 仍与 v2.2.0
一致。详见 [v3.0.0-beta.2 更新说明](RELEASE_NOTES_v3.0.0-beta.2.md)。

## Changelog / 更新日志

### v3.0.0-beta.2 — 2026-08-08

- Updated `rietveld-plotting` to detect single- versus multiphase GPX
  reflection lists automatically.
- Preserved the established green `Bragg position` row and legend for
  single-phase figures.
- Added deterministic phase-specific Bragg colors and exact GPX phase names in
  the upper-right legend for multiphase figures.
- Added optional `xx.xx wt%` legend suffixes only when GSAS-II supplies a
  complete, normalized, covariance-backed crystalline mass-fraction set.
- Recorded phase-color mappings, mass-fraction values, uncertainties, and
  source provenance in the optional plot manifest.

本版只更新画图 skill：单相图保持原样；多相图按物相区分 Bragg 颜色，并在
右上角显示对应物相名。只有 GSAS-II 返回完整、归一化且带协方差误差的晶态
质量分数时，图例才附加 wt%，不会用峰高或普通 Scale 值冒充相比例。

### v3.0.0-beta.1 — 2026-08-06

- Added `gsas-ii-multiphase-refinement` as an explicitly labelled **Beta**
  skill for declared crystalline-phase refinement, covariance-backed QPA,
  common-problem sensitivity checks, trace-phase screening, internal-standard
  amorphous calculations, constrained dopant-model grids, and audited
  multiphase sequential handoff.
- Upgraded `rietveld-plotting` with phase-separated Bragg rows, multiphase cell
  trajectories, covariance-backed phase-fraction figures, changing-phase-set
  gaps, and phase-boundary markers.
- Kept `gsas-ii-rietveld-refinement` byte-for-byte unchanged from v2.2.0.
- Retained the PolyForm Noncommercial License 1.0.0 and the existing required
  copyright notice. Commercial use still requires written authorization.

本预发布版只新增多相精修 Beta 并升级多相绘图；普通单相/顺序精修 skill
不变。多相结果必须保留 `pass`、`review`、`fail` 科学门槛，Beta 不负责未知
物相自动识别，也不默认代表结果已达到发表标准。

### v2.2.0 — 2026-08-01

- Added mandatory classification for single-pattern, independent-batch,
  sequential/operando, detector-integration, and plotting requests.
- Added audited temperature, time, voltage, capacity, and state-of-charge
  sequential refinement with anchor checkpoints and forward/reverse checks.
- Added one-to-one metadata synchronization, STOE frame conversion, sequential
  candidate selection, and machine-readable `pass`/`review`/`fail` auditing.
- Added separate read-only temperature/operando stack, contour, and trajectory
  plotting without merging visualization into refinement.
- Changed new-release licensing from MIT to PolyForm Noncommercial 1.0.0;
  commercial use now requires separate written authorization.

本版新增单谱、独立批次、原位顺序、探测器积分和绘图任务的强制分类；
新增温度、时间、电压、容量和 SOC 序列的锚点检查、正反向精修、一对一元数据
同步与机器审计；同时保持精修与绘图分离。新版自有代码改用 PolyForm
Noncommercial 1.0.0，商业使用需另行取得书面授权。

## Repository layout / 仓库结构

```text
gsas-ii-rietveld-refinement/  # single-pattern and sequential refinement
gsas-ii-multiphase-refinement/ # beta multiphase refinement and QPA
rietveld-plotting/            # read-only single/multiphase and series plotting
docs/                         # public validation record
tests/                        # repository-owned unit tests
```

The two refinement skills never generate figures. The plotting skill never
refines or saves a GPX.

两个精修 skill 均不生成图片；画图 skill 只读最终 GPX，不运行精修、不修改
GPX。

To install only this beta update while keeping the ordinary refinement skill at
v2.2.0, copy these two directories from the tagged checkout into the Codex
skills directory:

```bash
cp -R gsas-ii-multiphase-refinement "$HOME/.codex/skills/"
cp -R rietveld-plotting "$HOME/.codex/skills/"
```

Restart Codex after installation. Back up an existing `rietveld-plotting`
directory before replacing it. Do not copy `gsas-ii-rietveld-refinement` when
performing this two-skill beta update.

如果只更新本次 Beta，请仅复制“多相精修”和“画图”两个目录，并保留原有
v2.2.0 普通精修 skill。覆盖旧画图 skill 前先备份，安装后重启 Codex。

## Mandatory request classification / 强制任务分类

The first refinement action is now a deterministic routing gate, before
GSAS-II is imported or a GPX is created:

| Input/request | Route |
|---|---|
| One integrated 1D pattern | Deterministic single-pattern refinement |
| Ordered 1D series plus a valid manifest | Forward/reverse sequential refinement |
| Multiple independent patterns explicitly declared as a batch | Isolated single-pattern runs |
| Multiple patterns without a manifest or batch declaration | Stop and clarify |
| 2D detector frames | Stop and request calibrated 1D integration |
| Figure, heatmap, waterfall, or trajectory request | Separate plotting workflow |

The read-only classifier is
`gsas-ii-rietveld-refinement/scripts/classify_refinement_request.py`.
Shared routing and manifest preflight live in `refinement_core.py`. Both
numerical drivers repeat the gate and preserve `request_classification` in
their machine-readable plans.

现在第一步不是直接调用 GSAS-II，而是主动判定任务类别。多个谱图不会仅凭
文件名被自动当作原位序列；没有 manifest 时，必须先确认它们是独立样品还是
连续帧。二维探测器图像必须先完成有标定依据的一维积分，纯绘图任务则退出
精修 skill。

## Refinement features / 精修功能

- Calls real GSAS-II through `GSASIIscriptable`; simulated curves are not
  presented as refinement.
- Uses deterministic single-pattern branches for cell/Zero/profile sensitivity.
- Locks calibrated instrument peak-shape terms before testing geometry and
  lattice sensitivity.
- Keeps chemically sensitive coordinates, occupancies, dopant sites, and
  vacancy terms fixed unless independent evidence justifies them.
- Audits residual peaks, uncertainty formatting, R-factor labels,
  correlations, SVD warnings, and candidate path dependence.
- Writes `candidate_summary.json`, validates the canonical report, and uses
  transactional archive replacement for single-pattern results.
- Accepts explicitly declared phase sets in sequential runs. Quantitative phase
  fractions remain `review` unless constraints and formal uncertainties pass
  the audit. General multiphase QPA is routed to the separate beta skill.

- 通过 `GSASIIscriptable` 真实调用 GSAS-II，不把模拟曲线当作精修。
- 单谱精修采用确定性分阶段和分支比较，而不是按顺序盲目释放晶胞、Zero、
  U/V/W。
- 先锁定已校准的仪器峰形，再比较几何和晶胞敏感性。
- 默认不自由精修原子坐标、占位、掺杂位点和空位。
- 自动核验残差峰、不确定度、R 因子命名、参数相关性和候选路径依赖。
- 单谱结果保留 `candidate_summary.json`，报告经过机器校验，归档采用
  “验证输入 → 临时目录 → 哈希复核 → 原子替换”。
- 顺序精修可以读取显式声明的相集合；只有约束和正式不确定度均通过审计时，
  才能将相分数作为定量结果。一般多相 QPA 转交独立的 Beta skill。

## Multiphase refinement beta / 多相精修 Beta

`gsas-ii-multiphase-refinement` handles two or more **declared** crystalline
phases. It calls real GSAS-II, audits each CIF import, preserves calibrated
instrument broadening, uses identifiable scale parameterizations, extracts
GSAS-II mass fractions with covariance-backed uncertainties, and rebuilds the
selected model to test exact-state repeatability. It also contains explicit
sensitivity contracts for preferred orientation, microabsorption, trace phases,
internal-standard amorphous content, controlled cell release, and constrained
dopant-model grids.

This is a beta release. It does not identify unknown phases, prove dopant
content or site occupancy from an unconstrained fit, or automatically turn a
`review` result into a publishable quantitative claim. Declared-phase
multiphase sequential/operando work uses the companion v2.2.0 sequential
driver and a separate multiphase stage validator.

`gsas-ii-multiphase-refinement` 面向两个或以上已声明晶相，真实调用 GSAS-II，
核验 CIF 导入、仪器峰形、可辨识的 Scale 参数化、协方差相分数不确定度和最终
状态重复性。它还提供择优取向、微吸收、微量相、内标非晶、受控晶胞释放和
受约束掺杂模型网格的审计入口。该功能明确为 Beta：不自动识别未知相，不凭
自由占位精修证明掺杂量或占位，也不会把 `review` 自动包装成可发表结论。

## Operando and sequential mode / 原位与顺序精修

The sequential driver accepts ordered, already integrated one-dimensional
patterns with a CSV manifest. It:

1. preflights 1D patterns and validates frame IDs, order, metadata provenance,
   files, and SHA-256 hashes;
2. can join a frame index to experimental metadata by exact key or bounded
   one-to-one nearest time, without interpolation;
3. refines start/middle/end/requested/phase-boundary anchors and uses accepted
   internal anchors as actual checkpoints;
4. partitions forward and reverse runs into checkpoint segments so a failed
   segment is retained without erasing independent results;
5. keeps one common global reference cell and converts each checkpoint cell to
   the equivalent HAP `Dij/HStrain` seed;
6. runs bounded repeated stages: stable background/scale/Dij, optional sample
   geometry, constrained phase fractions plus justified size/microstrain, then
   explicitly gated atomic X/U terms;
7. exports per-frame cells, formal uncertainties when available, phase
   fractions, convergence, correlations, residual audits, JSON, and CSV;
8. compares forward/reverse sensitivity and persistent residual peaks;
9. writes a conservative multi-candidate `candidate_summary.json` that rejects
   any `fail` result and ranks stability before median Rwp;
10. stages verified inputs and produces no figure.

顺序精修面向已经积分好的一维原位/operando/温度/时间序列。它会严格读取
CSV 清单，先精修首帧、中间帧、末帧和转变区锚点，再从首末锚点分别运行
正向和反向序列。逐帧晶胞变化使用 `Dij/HStrain`，不会在顺序精修中错误
释放全局 `Cell`。所有结果按 `pass`、`review`、`fail` 分级；正反向差异、
高相关或 shift/esd 偏大都会保留供科研复核，不会被平滑或隐藏。

Manifest and workflow details:

- [Sequential manifest](gsas-ii-rietveld-refinement/references/sequential-manifest.md)
- [Sequential workflow](gsas-ii-rietveld-refinement/references/sequential-workflow.md)

## External validation / 外部验证

Sequential mode was developed against the official GSAS-II Sequential
Refinement tutorial and its 17-frame CuCr2O4/CuO temperature-series exercise:

- [Official tutorial](https://advancedphotonsource.github.io/GSAS-II-tutorials/SeqRefine/SequentialTutorial.htm)
- [Official exercise archive](https://advancedphotonsource.github.io/GSAS-II-tutorials/SeqRefine/data/SeqTut.zip)

The third-party tutorial files are not committed to this repository. They are
kept outside version control and used only for local validation.

官方训练数据不会提交进本仓库，只作为本地外部验证数据。

The exact test configuration, bounded stage-convergence improvement, audit
status, and remaining scientific limitations are recorded in
[Operando validation](docs/OPERANDO_VALIDATION.md). All 17 official exercise
frames completed in both directions and path sensitivity decreased, but a
persistent missing peak near 9.3 degrees triggers the hard residual gate.
The current phase model therefore remains `fail`, not an accepted result.

完整验证配置、分阶段收敛改进、审计结果和适用边界见
[原位顺序精修验证记录](docs/OPERANDO_VALIDATION.md)。当前官方序列 17 帧
均完整结束，双向路径差已明显降低；但约 9.3° 的持续漏峰触发模型硬门槛，
因此审计仍为 `fail`。软件完成不等于当前相模型已达到科学可接受状态。

## Local configuration / 本地配置

Use Python 3.10 or newer with NumPy and Matplotlib available for the plotting
and audit utilities. Install GSAS-II separately, then point the refinement and
single-pattern plotting routes to a compatible Python interpreter that can
import it:

```bash
export GSASII_DIR="/path/to/GSAS-II"
export GSASII_PYTHON="/path/to/python-that-imports-GSASII"
export GSASII_REFINEMENT_ARCHIVE="$HOME/GSAS-II_refinement_results"
export GSASII_REFINEMENT_STAGING="$HOME/GSAS-II_refinement_staging"
export RIETVELD_PLOT_OUTPUT="$HOME/Rietveld_plot_results"
```

Bring your own calibrated instrument file, CIF models, and diffraction data.
The repository contains no private experimental data, instrument files, or
personal absolute paths.

画图和审计脚本需要 Python 3.10 或以上版本、NumPy 与 Matplotlib；精修及
单谱 GPX 画图还需要单独安装 GSAS-II。使用时需自行准备校准仪器文件、CIF
和衍射数据。本仓库不包含私人原始数据、私人仪器参数或个人电脑绝对路径。

## Plotting split / 画图拆分

`rietveld-plotting` reads an accepted final GPX and creates the locked
publication-style single-pattern figure without modifying refinement
parameters. For multiphase GPX files it can place phase-labelled Bragg ticks on
separate rows. It also reads an audited temperature or operando sequential
result and generates a series-coloured stacked pattern, an intensity contour
map, per-phase refined cell trajectories, and covariance-backed phase-fraction
plots. Absent phases are shown as gaps rather than interpolated trajectories,
and changing phase sets receive explicit boundary markers. Constant-temperature
battery operando results use an audited time/voltage/capacity coordinate when
available, or honest frame order when synchronization is absent. The sequential
route verifies all recorded pattern and GPX hashes before and after plotting,
exports 600 dpi PNG plus editable SVG, and retains a machine-readable plot
manifest.

The temperature-series layout follows the typography hierarchy, boxed axes,
outward ticks, restrained colours, and export quality of the repository's
Origin-style contract. Its scientific layout follows published
variable-temperature and operando-XRD conventions, but no paper PDF or paper
data is redistributed. Raw patterns and refined trajectories are never
smoothed; contour resampling, global/per-frame normalization, and colour-limit
clipping are display-only operations recorded in the manifest.

`rietveld-plotting` 只负责从已接受的最终 GPX 或已审计的温度/原位顺序
精修结果生成论文风格图片。新增输出包含顺序堆叠谱、强度等高图和带
正式不确定度的晶胞参数轨迹。恒温电池原位在有同步证据时使用时间、电压或容量；
缺少同步信息时只使用真实帧序，不将帧号伪装为时间或电压。它在作图前后核对所有源谱和 GPX 哈希，
不平滑原始谱图或精修轨迹，并用 JSON 记录所有仅用于显示的归一化、插值
和色阶裁剪。显示方式不会反向影响精修参数、候选选择或审计结果。

## License / 许可证

Original code in this repository is licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE). Noncommercial use is permitted
under its exact terms. Commercial use requires a separate written license from
[R3ze959](https://github.com/R3ze959); contact the repository owner before use.
Because commercial use is restricted, this is a source-available release, not
an OSI-approved open-source release. Earlier versions already released under
the MIT License remain available under their original terms.

本仓库的自有代码采用 [PolyForm Noncommercial License 1.0.0](LICENSE)。
在该许可证的准确条款内可以非商业使用；商业使用必须事先向
[R3ze959](https://github.com/R3ze959) 取得单独的书面授权。由于限制商业使用，
本版本属于源代码可见软件（source-available），不属于 OSI 认可的开源软件。
已经以 MIT 许可证发布的旧版本仍继续适用原 MIT 条款。

This repository does not bundle or relicense GSAS-II. GSAS-II, third-party
libraries, and third-party datasets remain governed by their own licenses.

本仓库不捆绑、也不重新许可 GSAS-II。GSAS-II、第三方库和第三方数据集
分别遵循它们自身的许可证。
