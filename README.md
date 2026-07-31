# GSAS-II Rietveld Refinement Skills

Conservative Codex skills for real GSAS-II powder refinement and separate,
read-only publication plotting.

一个面向真实 GSAS-II 粉末精修的 Codex skill 仓库。精修与画图严格分离，
并新增了原位、operando、温度和时间序列的顺序精修模式。

## Repository layout / 仓库结构

```text
gsas-ii-rietveld-refinement/  # single-pattern and sequential refinement
rietveld-plotting/            # read-only plotting from an accepted GPX
docs/                         # design and validation notes
tests/                        # repository-owned unit tests
```

The refinement skill never generates figures. The plotting skill never refines
or saves a GPX.

精修 skill 不生成图片；画图 skill 只读最终 GPX，不运行精修、不修改 GPX。

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
- Supports multiphase models and constrained phase fractions.

- 通过 `GSASIIscriptable` 真实调用 GSAS-II，不把模拟曲线当作精修。
- 单谱精修采用确定性分阶段和分支比较，而不是按顺序盲目释放晶胞、Zero、
  U/V/W。
- 先锁定已校准的仪器峰形，再比较几何和晶胞敏感性。
- 默认不自由精修原子坐标、占位、掺杂位点和空位。
- 自动核验残差峰、不确定度、R 因子命名、参数相关性和候选路径依赖。
- 单谱结果保留 `candidate_summary.json`，报告经过机器校验，归档采用
  “验证输入 → 临时目录 → 哈希复核 → 原子替换”。
- 支持多相模型和受约束相分数。

## Operando and sequential mode / 原位与顺序精修

The sequential driver accepts ordered, already integrated one-dimensional
patterns with a CSV manifest. It:

1. validates frame IDs, order, metadata, files, and SHA-256 hashes;
2. refines representative start/middle/end/transition anchors;
3. blocks propagation when an endpoint anchor fails convergence, SVD,
   shift/esd, or Rwp/Rwp-min gates;
4. keeps the instrument profile locked and uses HAP `Dij/HStrain` rather than
   global cell refinement for frame-dependent lattice changes;
5. runs fixed stages: stable background/scale/Dij, optional sample geometry,
   constrained phase fractions plus size/microstrain, then optional atomic
   X/U terms;
6. runs forward from the first anchor and reverse from the last anchor;
7. exports covariance-backed per-frame cells, uncertainties, phase fractions,
   convergence, correlations, residual audits, JSON, and CSV;
8. distinguishes `pass`, `review`, and `fail` instead of treating every
   numerical completion as success;
9. stages verified copies of the manifest, instrument file, CIFs, and patterns;
10. produces no figure.

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

The exact test configuration, numeric reproducibility result, audit status, and
remaining scientific limitations are recorded in
[Operando validation](docs/OPERANDO_VALIDATION.md). The official exercise
completed twice with identical exported numeric results, but the audit remains
`review` because high correlations, large final shift/esd values, and
forward/reverse path dependence must not be hidden.

完整验证配置、两次运行的数值一致性、审计结果和适用边界见
[原位顺序精修验证记录](docs/OPERANDO_VALIDATION.md)。官方序列两次运行的
导出数值完全一致，但由于高相关、较大的最终 shift/esd 和正反向路径依赖，
审计结果仍是 `review`，不能表述为模型已无歧义。

## Local configuration / 本地配置

Install GSAS-II separately, then point the skills to a Python interpreter that
can import it:

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

使用时需自行准备 GSAS-II、校准仪器文件、CIF 和衍射数据。本仓库不包含
私人原始数据、私人仪器参数或个人电脑绝对路径。

## Plotting split / 画图拆分

`rietveld-plotting` reads an accepted final GPX and creates the locked
publication-style figure without modifying refinement parameters. This split
prevents display choices from contaminating model selection or numerical
audits.

`rietveld-plotting` 只负责从已接受的最终 GPX 生成论文风格图片。显示方式
不会反向影响精修参数、候选选择或审计结果。

## License / 许可证

Released under the MIT License. See [LICENSE](LICENSE).

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。
