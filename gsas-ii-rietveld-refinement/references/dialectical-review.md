# Dialectical review reference

Before selecting a final refinement result, run a self-debate. Write it in the report, even if brief.

## Roles

| Role | Question |
|---|---|
| Fitter | Why did this parameter/model improve the fit? |
| Skeptic | Could this improvement be overfit, nonphysical, or hiding a real problem? |
| Chemist | Does the result respect the composition, site chemistry, and known structure? |
| Reviewer | What would a skeptical reader object to? |

## Candidate review table

Use this table for each candidate:

| Check | Pass/fail | Notes |
|---|---|---|
| Rwp/Rp improves meaningfully |  | Compare against conservative baseline |
| Residual shape improves |  | Look at strong peaks and low-angle region |
| Bragg ticks explain peaks |  | Do not ignore unexplained peaks |
| Cell parameters remain plausible |  | Compare parent and doped samples |
| Profile terms remain physical |  | Reject negative or extreme terms unless justified |
| Parameter correlations acceptable |  | Read `.lst`; reject 100% correlation/SVD final candidates |
| Chemistry is respected |  | No free occupancy without constraints |
| Evidence boundary is clear |  | Do not overclaim dopant site or oxygen defects |

## Verdict rules

Use one of:

- `Recommended final`: stable, physically interpretable, and archived.
- `Conservative backup`: fewer parameters, slightly worse Rwp, safe for presentation.
- `Exploratory candidate`: useful for discussion, not final.
- `Rejected`: lower Rwp but nonphysical, unstable, or overfit.

If two candidates have nearly identical Rwp, choose the one with fewer parameters and fewer warnings.

## Report wording

Use cautious language:

- "The parent `I4/m` model explains the main peaks."
- "The refined cell suggests an average lattice change."
- "Microstrain is a peak-broadening model and a clue for local distortion, not direct quantitative proof."
- "This refinement does not prove dopant site occupancy without independent composition/structure evidence."
