"""Statistics + manuscript tools: power, assumptions, effect sizes, multiplicity, build, check.

Numerics here are **pure standard library** (``math``, ``statistics``,
``fractions``). The sidecar build excludes scipy/numpy/pandas, so nothing in
this module may import them — at module scope or inside a function. Every
numeric payload therefore names the algorithm that produced it (``method``)
and the tolerance it was controlled to (``accuracy``); an unstated
approximation is a bug.

What each primitive actually is
------------------------------
* Normal cdf / quantile — :class:`statistics.NormalDist`. CPython's
  ``NormalDist.inv_cdf`` implements Wichura's AS 241 rational approximation
  (about 16 significant digits), so we do not hand-roll Acklam here.
* Regularized incomplete beta ``I_x(a, b)`` — modified Lentz continued
  fraction, relative tolerance 1e-12, at most 300 iterations. Drives the
  Student-t and F cdfs.
* Regularized lower incomplete gamma ``P(a, x)`` — series for ``x < a + 1``,
  continued fraction above, relative tolerance 1e-12. Drives the chi-square cdf.
* Quantiles — bisection on the cdf with an expanding bracket, absolute
  tolerance 1e-10.
* Noncentral t / chi-square / F — Poisson-weighted mixtures of central terms,
  summed over a window around the Poisson mode in log space. The truncation
  error is bounded by the Poisson mass left outside the window, which is
  returned as ``residual_weight`` and folded into ``accuracy``.

Honesty note: the cdf tolerances above are the ones the iterations are driven
to. The *overall* error of a power number that inverts those cdfs is not
independently bounded here — the payload says so. Regression anchors from
published worked examples live in ``tests/test_agent_science_tools.py``.
"""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import statistics
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.errors import format_tool_error

_TAIL_CHARS = 6_000
_MAX_TABLE_ROWS = 200_000
_ND = statistics.NormalDist()

# ---------------------------------------------------------------------------
# distribution primitives (stdlib only)
# ---------------------------------------------------------------------------


def norm_cdf(x: float) -> float:
    """Standard normal cdf (statistics.NormalDist, double precision)."""
    return _ND.cdf(float(x))


def norm_ppf(p: float) -> float:
    """Standard normal quantile — Wichura AS 241 via ``NormalDist.inv_cdf``."""
    p = min(max(float(p), 1e-15), 1.0 - 1e-15)
    return _ND.inv_cdf(p)


def _betacf(a: float, b: float, x: float, *, rtol: float = 1e-12, max_iter: int = 300) -> float:
    """Continued fraction for the incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < rtol:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta ``I_x(a, b)``; Lentz CF, rtol 1e-12."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return max(0.0, min(1.0, front * _betacf(a, b, x) / a))
    return max(0.0, min(1.0, 1.0 - front * _betacf(b, a, 1.0 - x) / b))


def gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma ``P(a, x)``; series/CF, rtol 1e-12."""
    if x <= 0.0:
        return 0.0
    if x < a + 1.0:
        ap = a
        term = 1.0 / a
        total = term
        for _ in range(1000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-12:
                break
        return max(0.0, min(1.0, total * math.exp(-x + a * math.log(x) - math.lgamma(a))))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1001):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    return max(0.0, min(1.0, 1.0 - q))


def chi2_cdf(x: float, df: float) -> float:
    """Chi-square cdf via the regularized incomplete gamma."""
    if df <= 0:
        return 0.0
    return gamma_p(df / 2.0, max(0.0, x) / 2.0)


def t_cdf(t: float, df: float) -> float:
    """Student-t cdf via the regularized incomplete beta."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    half = 0.5 * betainc(df / 2.0, 0.5, x)
    return 1.0 - half if t > 0 else half


def f_cdf(f: float, df1: float, df2: float) -> float:
    """Snedecor F cdf via the regularized incomplete beta."""
    if f <= 0 or df1 <= 0 or df2 <= 0:
        return 0.0
    return betainc(df1 / 2.0, df2 / 2.0, df1 * f / (df1 * f + df2))


def _bisect_cdf(cdf: Any, p: float, lo: float, hi: float, *, tol: float = 1e-10) -> float:
    """Invert a monotone increasing cdf on ``[lo, hi]`` to absolute ``tol``."""
    for _ in range(400):
        mid = 0.5 * (lo + hi)
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def t_ppf(p: float, df: float) -> float:
    """Student-t quantile — bisection on :func:`t_cdf`, abs tol 1e-10."""
    p = min(max(float(p), 1e-14), 1.0 - 1e-14)
    return _bisect_cdf(lambda t: t_cdf(t, df), p, -1e4, 1e4)


def chi2_ppf(p: float, df: float) -> float:
    """Chi-square quantile — bisection on :func:`chi2_cdf`, abs tol 1e-10."""
    p = min(max(float(p), 1e-14), 1.0 - 1e-14)
    hi = max(10.0, df * 10.0 + 100.0)
    while chi2_cdf(hi, df) < p and hi < 1e9:
        hi *= 2.0
    return _bisect_cdf(lambda x: chi2_cdf(x, df), p, 0.0, hi)


def f_ppf(p: float, df1: float, df2: float) -> float:
    """F quantile — bisection on :func:`f_cdf`, abs tol 1e-10."""
    p = min(max(float(p), 1e-14), 1.0 - 1e-14)
    hi = 10.0
    while f_cdf(hi, df1, df2) < p and hi < 1e9:
        hi *= 2.0
    return _bisect_cdf(lambda x: f_cdf(x, df1, df2), p, 0.0, hi)


_MIX_CAP = 10_000


def _poisson_window(lam: float, cap: int = _MIX_CAP) -> tuple[int, int]:
    """Index window around the Poisson(lam) mode holding essentially all mass."""
    if lam <= 0:
        return 0, 0
    spread = 12.0 * math.sqrt(lam) + 30.0
    lo = max(0, int(lam - spread))
    hi = int(lam + spread) + 1
    if hi - lo > cap:
        hi = lo + cap
    return lo, hi


def _poisson_terms(lam: float) -> list[tuple[int, float]]:
    """(j, weight) for the Poisson(lam) window, computed in log space."""
    lo, hi = _poisson_window(lam)
    log_lam = math.log(lam) if lam > 0 else 0.0
    out: list[tuple[int, float]] = []
    for j in range(lo, hi + 1):
        logw = -lam + j * log_lam - math.lgamma(j + 1.0)
        if logw < -745.0:
            continue
        out.append((j, math.exp(logw)))
    return out


def nct_cdf(t: float, df: float, ncp: float) -> float:
    """Noncentral t cdf — Poisson mixture of incomplete betas (Lenth 1989 form).

    ``F(t) = Phi(-d) + 0.5 * sum_j [p_j I_x(j+1/2, df/2) + q_j I_x(j+1, df/2)]``
    with ``x = t^2/(t^2+df)``. Negative ``t`` uses ``F(t; d) = 1 - F(-t; -d)``.
    """
    if ncp == 0.0:
        return t_cdf(t, df)
    if t < 0.0:
        return 1.0 - nct_cdf(-t, df, -ncp)
    lam = ncp * ncp / 2.0
    if lam > 1e6:  # every usable term underflows; the normal limit is honest here
        se = math.sqrt(1.0 + t * t / (2.0 * df)) if df > 0 else 1.0
        return norm_cdf((t - ncp) / se)
    x = (t * t) / (t * t + df) if (t * t + df) > 0 else 0.0
    total = norm_cdf(-ncp)
    log_lam = math.log(lam)
    covered = 0.0
    lo, hi = _poisson_window(lam)
    for j in range(lo, hi + 1):
        logp = -lam + j * log_lam - math.lgamma(j + 1.0)
        if logp < -745.0:
            continue
        pj = math.exp(logp)
        covered += pj
        qj = math.exp(-lam + j * log_lam - math.lgamma(j + 1.5)) * ncp / math.sqrt(2.0)
        total += 0.5 * (pj * betainc(j + 0.5, df / 2.0, x) + qj * betainc(j + 1.0, df / 2.0, x))
    del covered
    return max(0.0, min(1.0, total))


def ncchi2_cdf(x: float, df: float, lam: float) -> float:
    """Noncentral chi-square cdf — Poisson(lam/2) mixture of central chi-squares."""
    if lam <= 0:
        return chi2_cdf(x, df)
    if x <= 0:
        return 0.0
    total = 0.0
    for j, w in _poisson_terms(lam / 2.0):
        total += w * gamma_p(df / 2.0 + j, x / 2.0)
    return max(0.0, min(1.0, total))


def ncf_cdf(f: float, df1: float, df2: float, lam: float) -> float:
    """Noncentral F cdf — Poisson(lam/2) mixture of incomplete betas."""
    if lam <= 0:
        return f_cdf(f, df1, df2)
    if f <= 0:
        return 0.0
    y = df1 * f / (df1 * f + df2)
    total = 0.0
    for j, w in _poisson_terms(lam / 2.0):
        total += w * betainc(df1 / 2.0 + j, df2 / 2.0, y)
    return max(0.0, min(1.0, total))


# ---------------------------------------------------------------------------
# power / sample size
# ---------------------------------------------------------------------------

POWER_TESTS = (
    "one_sample_t",
    "two_sample_t",
    "paired_t",
    "anova_oneway",
    "one_proportion",
    "two_proportions",
    "chi_square_gof",
    "chi_square_independence",
    "correlation",
    "log_rank",
)

_ALTERNATIVES = ("two_sided", "greater", "less")

_METHOD_NOTES = {
    "one_sample_t": (
        "noncentral t (Poisson mixture of incomplete betas), ncp = d*sqrt(n)",
        "cdf iterations controlled to rtol 1e-12; critical value inverted to 1e-10; "
        "overall power error not independently bounded",
    ),
    "paired_t": (
        "noncentral t (Poisson mixture of incomplete betas), ncp = dz*sqrt(n_pairs)",
        "cdf iterations controlled to rtol 1e-12; critical value inverted to 1e-10; "
        "overall power error not independently bounded",
    ),
    "two_sample_t": (
        "noncentral t (Poisson mixture of incomplete betas), "
        "ncp = d*sqrt(n1*n2/(n1+n2)), df = n1+n2-2",
        "cdf iterations controlled to rtol 1e-12; critical value inverted to 1e-10; "
        "assumes equal variances (Welch reduces df and slightly lowers power)",
    ),
    "anova_oneway": (
        "noncentral F (Poisson mixture of incomplete betas), lambda = f^2 * N_total",
        "cdf iterations controlled to rtol 1e-12; omnibus power only — it is not the "
        "power of any single pairwise contrast",
    ),
    "one_proportion": (
        "normal approximation to the binomial (unpooled alternative variance)",
        "approximate: the true size and power of an exact binomial test are stepped, "
        "so this is optimistic by up to a few percent for n < 50 or p near 0/1",
    ),
    "two_proportions": (
        "normal approximation with pooled null variance "
        "(Cohen's arcsine h when explicit proportions are not given)",
        "approximate: no continuity correction, so n is 3-6% smaller than the "
        "Fleiss continuity-corrected formula; exact (Fisher) tests need more",
    ),
    "chi_square_gof": (
        "noncentral chi-square, lambda = w^2 * N",
        "cdf iterations controlled to rtol 1e-12; the chi-square reference "
        "distribution itself is asymptotic and unreliable when expected cells < 5",
    ),
    "chi_square_independence": (
        "noncentral chi-square, lambda = w^2 * N",
        "cdf iterations controlled to rtol 1e-12; the chi-square reference "
        "distribution itself is asymptotic and unreliable when expected cells < 5",
    ),
    "correlation": (
        "Fisher z transformation with normal reference, se = 1/sqrt(n-3)",
        "approximate: exact-r procedures (e.g. G*Power's exact option) can differ by "
        "about one observation; the bias correction r/(2(n-1)) is NOT applied",
    ),
    "log_rank": (
        "Schoenfeld's asymptotic formula for the number of events, "
        "events = (z_a + z_b)^2 / (p1*p2*ln(HR)^2)",
        "approximate and asymptotic: it assumes proportional hazards and returns "
        "EVENTS, not participants; converting to participants needs the event "
        "probability, which the tool will not guess",
    ),
}

_ASSUMPTIONS = {
    "one_sample_t": [
        "observations independent",
        "the sampling distribution of the mean is approximately normal",
        "the effect size is Cohen's d = (mu - mu0) / sigma",
    ],
    "paired_t": [
        "pairs independent of each other",
        "differences approximately normal",
        "the effect size is dz = mean(difference) / sd(difference), NOT between-group d",
    ],
    "two_sample_t": [
        "the two groups are independent",
        "approximately normal within group (or n large enough for the CLT)",
        "equal variances (this is the pooled t; Welch costs a little power)",
        "the effect size is Cohen's d on the pooled sd",
    ],
    "anova_oneway": [
        "independent groups, equal cell sizes",
        "approximately normal residuals, homogeneous variances",
        "the effect size is Cohen's f (f = sqrt(eta^2 / (1 - eta^2)))",
    ],
    "one_proportion": [
        "independent Bernoulli trials",
        "normal approximation to the binomial (poor for small n or extreme p)",
    ],
    "two_proportions": [
        "two independent samples of independent Bernoulli trials",
        "normal approximation; no continuity correction applied",
    ],
    "chi_square_gof": [
        "independent observations, expected count >= 5 in essentially every cell",
        "the effect size is Cohen's w",
    ],
    "chi_square_independence": [
        "independent observations, expected count >= 5 in essentially every cell",
        "the effect size is Cohen's w (= phi for a 2x2 table)",
    ],
    "correlation": [
        "independent pairs, bivariate normality",
        "linear association — a curved relation is not measured by r",
    ],
    "log_rank": [
        "proportional hazards over the whole follow-up",
        "independent censoring",
        "the result is a number of EVENTS; participants depend on accrual and follow-up",
    ],
}


def _z_alpha(alpha: float, alternative: str) -> float:
    return norm_ppf(1.0 - alpha / 2.0) if alternative == "two_sided" else norm_ppf(1.0 - alpha)


def _t_power(effect: float, n1: float, n2: float, alpha: float, alternative: str) -> float:
    """Power of a t test given the ncp implied by (effect, n1, n2)."""
    if n2 > 0:
        df = n1 + n2 - 2.0
        ncp = effect * math.sqrt(n1 * n2 / (n1 + n2))
    else:
        df = n1 - 1.0
        ncp = effect * math.sqrt(n1)
    if df <= 0:
        return 0.0
    if alternative == "two_sided":
        crit = t_ppf(1.0 - alpha / 2.0, df)
        return (1.0 - nct_cdf(crit, df, ncp)) + nct_cdf(-crit, df, ncp)
    if alternative == "greater":
        return 1.0 - nct_cdf(t_ppf(1.0 - alpha, df), df, ncp)
    return nct_cdf(t_ppf(alpha, df), df, ncp)


def _parse_floats(raw: str) -> list[float]:
    """Accept a JSON array or a comma/whitespace separated list of numbers."""
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = []
        return [float(v) for v in data if isinstance(v, int | float)]
    out: list[float] = []
    for chunk in re.split(r"[,\s;]+", text):
        if not chunk:
            continue
        try:
            out.append(float(chunk))
        except ValueError:
            continue
    return out


def _cohen_h(p1: float, p2: float) -> float:
    return 2.0 * math.asin(math.sqrt(p1)) - 2.0 * math.asin(math.sqrt(p2))


def power_for(
    test: str,
    *,
    n: float,
    effect_size: float,
    alpha: float,
    alternative: str = "two_sided",
    n2: float = 0.0,
    groups: int = 2,
    df: float = 0.0,
    props: list[float] | None = None,
    ratio: float = 1.0,
) -> float:
    """Power of *test* at the given design. ``n`` is per group where that applies."""
    props = props or []
    alpha = min(max(float(alpha), 1e-12), 0.999999)
    if test in ("one_sample_t", "paired_t"):
        return _t_power(effect_size, max(2.0, n), 0.0, alpha, alternative)
    if test == "two_sample_t":
        nb = n2 if n2 > 0 else n * (ratio if ratio > 0 else 1.0)
        return _t_power(effect_size, max(2.0, n), max(2.0, nb), alpha, alternative)
    if test == "anova_oneway":
        k = max(2, int(groups))
        total = n * k
        df1 = k - 1.0
        df2 = total - k
        if df2 <= 0:
            return 0.0
        lam = effect_size * effect_size * total
        return 1.0 - ncf_cdf(f_ppf(1.0 - alpha, df1, df2), df1, df2, lam)
    if test == "one_proportion":
        za = _z_alpha(alpha, alternative)
        if len(props) >= 2:
            p0, p1 = props[0], props[1]
            se0 = math.sqrt(max(1e-12, p0 * (1.0 - p0)))
            se1 = math.sqrt(max(1e-12, p1 * (1.0 - p1)))
            delta = p1 - p0
            if alternative == "less":
                delta = -delta
            elif alternative == "two_sided":
                delta = abs(delta)
            return norm_cdf((delta * math.sqrt(n) - za * se0) / se1)
        signed = effect_size if alternative != "two_sided" else abs(effect_size)
        if alternative == "less":
            signed = -signed
        return norm_cdf(signed * math.sqrt(n) - za)
    if test == "two_proportions":
        za = _z_alpha(alpha, alternative)
        nb = n2 if n2 > 0 else n * (ratio if ratio > 0 else 1.0)
        if len(props) >= 2:
            p1, p2 = props[0], props[1]
            pbar = (p1 * n + p2 * nb) / (n + nb)
            se0 = math.sqrt(max(1e-12, pbar * (1.0 - pbar) * (1.0 / n + 1.0 / nb)))
            se1 = math.sqrt(
                max(1e-12, p1 * (1.0 - p1) / n + p2 * (1.0 - p2) / nb)
            )
            delta = p1 - p2
            if alternative == "less":
                delta = -delta
            elif alternative == "two_sided":
                delta = abs(delta)
            return norm_cdf((delta - za * se0) / se1)
        se = math.sqrt(1.0 / n + 1.0 / nb)
        signed = effect_size if alternative != "two_sided" else abs(effect_size)
        if alternative == "less":
            signed = -signed
        return norm_cdf(signed / se - za)
    if test in ("chi_square_gof", "chi_square_independence"):
        dfree = float(df) if df > 0 else float(max(1, int(groups) - 1))
        total = n
        lam = effect_size * effect_size * total
        return 1.0 - ncchi2_cdf(chi2_ppf(1.0 - alpha, dfree), dfree, lam)
    if test == "correlation":
        if n <= 4:
            return 0.0
        r = min(max(effect_size, -0.999999), 0.999999)
        if alternative == "two_sided":
            r = abs(r)
        elif alternative == "less":
            r = -r
        zr = math.atanh(r)
        za = _z_alpha(alpha, alternative)
        return norm_cdf(zr * math.sqrt(n - 3.0) - za)
    if test == "log_rank":
        hr = effect_size
        if hr <= 0:
            return alpha
        p1 = 1.0 / (1.0 + (ratio if ratio > 0 else 1.0))
        p2 = 1.0 - p1
        za = _z_alpha(alpha, alternative)
        logged = math.log(hr)
        if alternative == "two_sided":
            logged = abs(logged)
        elif alternative == "less":
            logged = -logged
        if hr == 1.0:
            return alpha
        return norm_cdf(logged * math.sqrt(n * p1 * p2) - za)
    raise ValueError(f"unknown test: {test}")


def _min_n(test: str) -> float:
    if test == "correlation":
        return 5.0
    if test in ("chi_square_gof", "chi_square_independence", "log_rank"):
        return 2.0
    return 2.0


def solve_n(target_power: float, test: str, **kw: Any) -> int:
    """Smallest integer n (per group where that applies) reaching *target_power*."""
    lo = _min_n(test)
    if power_for(test, n=lo, **kw) >= target_power:
        return int(lo)
    hi = max(lo * 2.0, 8.0)
    while power_for(test, n=hi, **kw) < target_power:
        hi *= 2.0
        if hi > 5e7:
            return -1
    low, high = int(lo), int(math.ceil(hi))
    while low < high:
        mid = (low + high) // 2
        if power_for(test, n=float(mid), **kw) >= target_power:
            high = mid
        else:
            low = mid + 1
    return low


def solve_effect(target_power: float, test: str, **kw: Any) -> float:
    """Effect size reaching *target_power* at the given n (bisection, tol 1e-9)."""
    log_scale = test == "log_rank"

    def at(value: float) -> float:
        eff = math.exp(-value) if log_scale else value
        return power_for(test, effect_size=eff, **kw)

    lo, hi = 1e-9, 50.0
    if at(hi) < target_power:
        return float("nan")
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if at(mid) < target_power:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-9:
            break
    mid = 0.5 * (lo + hi)
    return math.exp(-mid) if log_scale else mid


def solve_alpha(target_power: float, test: str, **kw: Any) -> float:
    """Alpha at which the design reaches *target_power* (bisection, tol 1e-12)."""
    lo, hi = 1e-12, 0.999999
    if power_for(test, alpha=hi, **kw) < target_power:
        return float("nan")
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if power_for(test, alpha=mid, **kw) < target_power:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# multiplicity (exact algorithms, no approximation)
# ---------------------------------------------------------------------------

MULTIPLICITY_METHODS = ("bonferroni", "holm", "hochberg", "bh", "by", "none")


def adjust_pvalues(pvalues: list[float], method: str) -> list[float]:
    """Adjusted p-values, returned in the input order. Exact step algorithms."""
    m = len(pvalues)
    if m == 0:
        return []
    method = (method or "holm").strip().lower()
    idx = sorted(range(m), key=lambda i: pvalues[i])
    ordered = [pvalues[i] for i in idx]
    adj = [0.0] * m
    if method == "none":
        return list(pvalues)
    if method == "bonferroni":
        return [min(1.0, m * p) for p in pvalues]
    if method == "holm":
        running = 0.0
        for rank, p in enumerate(ordered):
            running = max(running, min(1.0, (m - rank) * p))
            adj[idx[rank]] = running
        return adj
    if method == "hochberg":
        running = 1.0
        for rank in range(m - 1, -1, -1):
            running = min(running, min(1.0, (m - rank) * ordered[rank]))
            adj[idx[rank]] = running
        return adj
    if method in ("bh", "by"):
        factor = 1.0
        if method == "by":
            factor = sum(1.0 / i for i in range(1, m + 1))
        running = 1.0
        for rank in range(m - 1, -1, -1):
            value = min(1.0, factor * m / (rank + 1) * ordered[rank])
            running = min(running, value)
            adj[idx[rank]] = running
        return adj
    raise ValueError(f"unknown method: {method}")


# ---------------------------------------------------------------------------
# tabular input (pure stdlib csv — the sidecar has no pandas)
# ---------------------------------------------------------------------------


def _sniff_delimiter(sample: str, path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".tsv", ".tab"):
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] else ","


def read_table(path: Path, max_rows: int = _MAX_TABLE_ROWS) -> tuple[list[str], list[list[str]], bool]:
    """(header, rows, truncated) from a delimited text file, stdlib ``csv`` only."""
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(64_000)
        fh.seek(0)
        reader = csv.reader(fh, delimiter=_sniff_delimiter(sample, path))
        try:
            header = next(reader)
        except StopIteration:
            return [], [], False
        rows: list[list[str]] = []
        truncated = False
        for row in reader:
            if len(rows) >= max_rows:
                truncated = True
                break
            rows.append(row)
    return [h.strip() for h in header], rows, truncated


def _column(header: list[str], rows: list[list[str]], name: str) -> list[str]:
    try:
        i = header.index(name)
    except ValueError:
        return []
    return [r[i].strip() if i < len(r) else "" for r in rows]


def _to_floats(values: list[str]) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _grouped(
    header: list[str], rows: list[list[str]], outcome: str, group: str
) -> dict[str, list[float]]:
    try:
        oi = header.index(outcome)
        gi = header.index(group)
    except ValueError:
        return {}
    out: dict[str, list[float]] = {}
    for r in rows:
        if oi >= len(r) or gi >= len(r):
            continue
        try:
            value = float(r[oi])
        except (TypeError, ValueError):
            continue
        out.setdefault(r[gi].strip(), []).append(value)
    return out


def _parse_values(raw: str) -> dict[str, list[float]] | list[float] | dict[str, float] | None:
    """``values`` accepts a JSON array, array-of-arrays, or object of groups."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith(("[", "{")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            if all(isinstance(v, list) for v in data.values()):
                return {k: [float(x) for x in v] for k, v in data.items()}
            return {k: float(v) for k, v in data.items() if isinstance(v, int | float)}
        if isinstance(data, list) and data and isinstance(data[0], list):
            return {f"group{i + 1}": [float(x) for x in g] for i, g in enumerate(data)}
        return [float(x) for x in data if isinstance(x, int | float)]
    return _parse_floats(text)


# ---------------------------------------------------------------------------
# assumption checks
# ---------------------------------------------------------------------------

NORMALITY_MIN_N = 20


def dagostino_pearson(values: list[float]) -> dict[str, Any]:
    """D'Agostino-Pearson omnibus K^2 (skewness Z + kurtosis Z), chi-square 2 df.

    Refuses below :data:`NORMALITY_MIN_N`: the transformed skew/kurtosis Zs are
    not usable at small n, and a p-value there would be theatre.
    """
    n = len(values)
    if n < NORMALITY_MIN_N:
        return {
            "name": "normality_of_residuals",
            "statistic": "DAgostino-Pearson K2",
            "value": None,
            "p": None,
            "verdict": "not_run",
            "detail": (
                f"n={n} is below {NORMALITY_MIN_N}; the skewness/kurtosis "
                "transformations are unreliable there. Judge normality from the "
                "design and a QQ plot instead."
            ),
        }
    mean = statistics.fmean(values)
    m2 = sum((v - mean) ** 2 for v in values) / n
    if m2 <= 0:
        return {
            "name": "normality_of_residuals",
            "statistic": "DAgostino-Pearson K2",
            "value": None,
            "p": None,
            "verdict": "not_run",
            "detail": "zero variance: every value is identical",
        }
    m3 = sum((v - mean) ** 3 for v in values) / n
    m4 = sum((v - mean) ** 4 for v in values) / n
    g1 = m3 / m2**1.5
    y = g1 * math.sqrt((n + 1) * (n + 3) / (6.0 * (n - 2)))
    beta2 = (
        3.0 * (n * n + 27.0 * n - 70.0) * (n + 1) * (n + 3)
        / ((n - 2.0) * (n + 5.0) * (n + 7.0) * (n + 9.0))
    )
    w2 = -1.0 + math.sqrt(2.0 * (beta2 - 1.0))
    w = math.sqrt(w2)
    delta = 1.0 / math.sqrt(math.log(w))
    a = math.sqrt(2.0 / (w2 - 1.0))
    zs = delta * math.asinh(y / a) if y != 0 else 0.0
    b2 = m4 / (m2 * m2)
    e_b2 = 3.0 * (n - 1.0) / (n + 1.0)
    var_b2 = 24.0 * n * (n - 2.0) * (n - 3.0) / ((n + 1.0) ** 2 * (n + 3.0) * (n + 5.0))
    x = (b2 - e_b2) / math.sqrt(var_b2)
    sqrt_beta1 = (
        6.0 * (n * n - 5.0 * n + 2.0) / ((n + 7.0) * (n + 9.0))
        * math.sqrt(6.0 * (n + 3.0) * (n + 5.0) / (n * (n - 2.0) * (n - 3.0)))
    )
    a_k = 6.0 + (8.0 / sqrt_beta1) * (
        2.0 / sqrt_beta1 + math.sqrt(1.0 + 4.0 / (sqrt_beta1 * sqrt_beta1))
    )
    inner = (1.0 - 2.0 / a_k) / (1.0 + x * math.sqrt(2.0 / (a_k - 4.0)))
    zk = ((1.0 - 2.0 / (9.0 * a_k)) - math.copysign(abs(inner) ** (1.0 / 3.0), inner)) / math.sqrt(
        2.0 / (9.0 * a_k)
    )
    k2 = zs * zs + zk * zk
    p = 1.0 - chi2_cdf(k2, 2)
    return {
        "name": "normality_of_residuals",
        "statistic": "DAgostino-Pearson K2 (chi-square, 2 df)",
        "value": round(k2, 6),
        "p": round(p, 6),
        "skewness": round(g1, 6),
        "kurtosis_excess": round(b2 - 3.0, 6),
        "verdict": "flag" if p < 0.05 else "no_flag",
        "detail": (
            "A significant K2 says the sample deviates from normal, not that the "
            "test you plan is invalid: normality tests are underpowered at small n "
            "and over-sensitive at large n. The QQ shape and the design matter more "
            "than this p-value."
        ),
    }


def brown_forsythe(groups: dict[str, list[float]]) -> dict[str, Any]:
    """Brown-Forsythe (Levene on group medians) test of variance homogeneity."""
    usable = {k: v for k, v in groups.items() if len(v) >= 2}
    if len(usable) < 2:
        return {
            "name": "variance_homogeneity",
            "statistic": "Brown-Forsythe F",
            "value": None,
            "p": None,
            "verdict": "not_run",
            "detail": "needs at least two groups with n >= 2",
        }
    z: dict[str, list[float]] = {}
    for name, values in usable.items():
        med = statistics.median(values)
        z[name] = [abs(v - med) for v in values]
    total = [v for values in z.values() for v in values]
    grand = statistics.fmean(total)
    k = len(z)
    n = len(total)
    between = sum(len(v) * (statistics.fmean(v) - grand) ** 2 for v in z.values())
    within = sum((x - statistics.fmean(v)) ** 2 for v in z.values() for x in v)
    if within <= 0 or n - k <= 0:
        return {
            "name": "variance_homogeneity",
            "statistic": "Brown-Forsythe F",
            "value": None,
            "p": None,
            "verdict": "not_run",
            "detail": "within-group spread of |x - median| is zero",
        }
    f = (between / (k - 1)) / (within / (n - k))
    p = 1.0 - f_cdf(f, k - 1.0, n - k)
    return {
        "name": "variance_homogeneity",
        "statistic": f"Brown-Forsythe F({k - 1}, {n - k})",
        "value": round(f, 6),
        "p": round(p, 6),
        "verdict": "flag" if p < 0.05 else "no_flag",
        "detail": (
            "Unequal variances do not forbid a comparison — they forbid the pooled "
            "one. Welch's t / Welch's ANOVA is the usual answer and costs little."
        ),
    }


def outlier_scan(values: list[float], *, cap: int = 40) -> dict[str, Any]:
    """IQR fence (1.5x) plus MAD z (|z| > 3.5); rows are listed, never dropped."""
    n = len(values)
    if n < 4:
        return {
            "name": "outliers",
            "statistic": "IQR fence + MAD z",
            "value": None,
            "p": None,
            "verdict": "not_run",
            "detail": "fewer than 4 values",
        }
    ordered = sorted(values)
    q1, _, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    med = statistics.median(ordered)
    mad = statistics.median([abs(v - med) for v in values])
    flagged: list[dict[str, Any]] = []
    for i, v in enumerate(values):
        by_iqr = iqr > 0 and (v < lo or v > hi)
        mz = 0.6745 * (v - med) / mad if mad > 0 else 0.0
        by_mad = mad > 0 and abs(mz) > 3.5
        if by_iqr or by_mad:
            flagged.append(
                {"row": i, "value": v, "iqr_fence": by_iqr, "mad_z": round(mz, 4)}
            )
    return {
        "name": "outliers",
        "statistic": "IQR 1.5x fence + MAD z (|z| > 3.5)",
        "value": len(flagged),
        "p": None,
        "verdict": "flag" if flagged else "no_flag",
        "rows": flagged[:cap],
        "truncated": len(flagged) > cap,
        "detail": (
            "These are candidates for inspection, not for deletion. Removing a point "
            "because it is inconvenient is a decision that has to be pre-specified "
            "and reported."
        ),
    }


# ---------------------------------------------------------------------------
# design -> defensible test choices
# ---------------------------------------------------------------------------

_OUTCOME_TYPES = (
    "continuous",
    "binary",
    "count",
    "ordinal",
    "nominal",
    "time_to_event",
    "proportion",
)


def _recommend(
    outcome_type: str,
    n_groups: int,
    *,
    paired: bool,
    repeated: bool,
    covariates: int,
    correlational: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(recommended, fallbacks) for a described design. Never guesses the design."""
    rec: list[dict[str, str]] = []
    alt: list[dict[str, str]] = []

    def add(where: list[dict[str, str]], test: str, why: str, fails: str) -> None:
        where.append({"test": test, "why": why, "when_it_fails": fails})

    if outcome_type == "continuous":
        if correlational:
            add(rec, "Pearson correlation", "two continuous variables, linear association",
                "non-linear or heavy-tailed: use Spearman rho or a rank/robust method")
            add(alt, "Spearman rank correlation", "monotone but non-linear association",
                "gives a rank statistic, not a slope in the outcome's units")
        elif covariates > 0:
            add(rec, "linear model (ANCOVA / multiple regression)",
                f"{covariates} covariate(s) to adjust for with a continuous outcome",
                "residual non-normality, heteroscedasticity, collinearity, or a "
                "covariate measured after randomisation (that one is a collider)")
            add(alt, "robust regression (Huber / sandwich SE)",
                "heteroscedastic or contaminated residuals",
                "still assumes the mean model is right")
        elif repeated:
            add(rec, "linear mixed-effects model (random intercept per subject)",
                "repeated measurements are not independent observations",
                "misspecified random-effect structure; the df/p depend on the "
                "approximation (Satterthwaite/Kenward-Roger) — report which")
            add(alt, "repeated-measures ANOVA with sphericity correction",
                "balanced, complete data only",
                "any missing timepoint drops the whole subject")
            add(alt, "Friedman test", "ordinal or non-normal repeated measures",
                "omnibus only; needs a post-hoc with multiplicity control")
        elif paired and n_groups == 2:
            add(rec, "paired t test", "each unit contributes both measurements",
                "differences clearly non-normal at small n")
            add(alt, "Wilcoxon signed-rank", "non-normal differences",
                "tests the distribution of differences, not the mean difference")
        elif n_groups == 1:
            add(rec, "one-sample t test", "one group against a fixed reference value",
                "non-normal at small n")
            add(alt, "Wilcoxon signed-rank / bootstrap CI", "non-normal at small n",
                "different estimand (median / resampled mean)")
        elif n_groups == 2:
            add(rec, "Welch's two-sample t test",
                "two independent groups; Welch is the safe default because it does "
                "not assume equal variances",
                "strong skew or extreme outliers at small n")
            add(alt, "Mann-Whitney U", "clearly non-normal, similar shapes",
                "tests stochastic dominance, not the difference in means")
            add(alt, "permutation test on the mean difference", "small n, any shape",
                "needs exchangeability under the null")
        else:
            add(rec, "one-way ANOVA (Welch's ANOVA if variances differ)",
                f"{n_groups} independent groups, continuous outcome",
                "unequal variances with unequal n; strong non-normality")
            add(alt, "Kruskal-Wallis", "non-normal groups",
                "omnibus on ranks; post-hoc still needs multiplicity control")
    elif outcome_type in ("binary", "proportion"):
        if paired:
            add(rec, "McNemar's test", "paired binary outcomes (same units twice)",
                "few discordant pairs: use the exact binomial version")
        elif n_groups <= 1:
            add(rec, "exact binomial test", "one proportion against a reference",
                "nothing much; it is exact")
        else:
            add(rec, "chi-square test of independence",
                f"{n_groups} independent groups, binary outcome",
                "expected cell counts below ~5: use Fisher's exact")
            add(alt, "Fisher's exact test", "small or sparse tables",
                "conservative; conditions on the margins")
        if covariates > 0:
            add(rec, "logistic regression", "binary outcome with covariates",
                "separation, sparse cells, or fewer than ~10 events per predictor")
    elif outcome_type == "count":
        add(rec, "Poisson regression (with offset for exposure time)",
            "counts with an exposure denominator",
            "variance > mean (overdispersion) makes the SEs too small")
        add(alt, "negative binomial regression", "overdispersed counts",
            "still assumes the mean model is right")
        add(alt, "zero-inflated / hurdle model", "far more zeros than Poisson allows",
            "two processes now need justifying, not just fitting")
    elif outcome_type == "ordinal":
        if paired:
            add(rec, "Wilcoxon signed-rank", "paired ordinal responses",
                "many ties")
        else:
            add(rec, "ordinal (proportional-odds) logistic regression",
                "ordered categories with covariates",
                "the proportional-odds assumption — test it")
            add(alt, "Mann-Whitney U / Kruskal-Wallis", "two or more groups, no covariates",
                "gives no adjusted effect estimate")
    elif outcome_type == "nominal":
        add(rec, "chi-square test of independence", "unordered categories by group",
            "sparse expected counts: Fisher / Monte-Carlo exact")
        add(alt, "multinomial logistic regression", "covariates needed",
            "needs a lot of data per category")
    elif outcome_type == "time_to_event":
        add(rec, "Kaplan-Meier + log-rank test", "censored time-to-event by group",
            "crossing survival curves break the log-rank's power")
        add(rec, "Cox proportional-hazards model", "covariate adjustment",
            "non-proportional hazards — check Schoenfeld residuals and say so")
        add(alt, "restricted mean survival time (RMST)", "hazards clearly not proportional",
            "depends on the truncation time you choose — pre-specify it")
    return rec, alt


# ---------------------------------------------------------------------------
# effect sizes
# ---------------------------------------------------------------------------

EFFECT_KINDS = (
    "cohens_d",
    "hedges_g",
    "cohens_dz",
    "glass_delta",
    "cliffs_delta",
    "pearson_r",
    "odds_ratio",
    "risk_ratio",
    "risk_difference",
    "nnt",
    "cramers_v",
    "eta_squared",
    "partial_eta_squared",
    "omega_squared",
)

_BENCHMARK_CAVEAT = (
    "'small / medium / large' are Cohen's field-relative conventions, not facts about "
    "your outcome. Report the effect in the outcome's own units as well, and compare it "
    "with effects that matter in this literature rather than with a label."
)


def _solve_ncp(cdf: Any, target: float, lo: float, hi: float) -> float:
    """Invert a cdf that DECREASES in the noncentrality parameter."""
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if cdf(mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-9:
            break
    return 0.5 * (lo + hi)


def _nct_ncp_ci(t: float, df: float, conf: float) -> tuple[float, float]:
    """CI for the noncentrality of a t statistic by noncentral-t inversion."""
    alpha = 1.0 - conf
    span = max(60.0, abs(t) * 4.0 + 30.0)
    low = _solve_ncp(lambda d: nct_cdf(t, df, d), 1.0 - alpha / 2.0, -span, span)
    high = _solve_ncp(lambda d: nct_cdf(t, df, d), alpha / 2.0, -span, span)
    return low, high


def _hedges_j(df: float) -> float:
    """Exact small-sample correction J = Gamma(df/2) / (sqrt(df/2) Gamma((df-1)/2))."""
    if df <= 1:
        return 1.0
    return math.exp(
        math.lgamma(df / 2.0) - 0.5 * math.log(df / 2.0) - math.lgamma((df - 1.0) / 2.0)
    )


def _two_group_summary(
    groups: dict[str, list[float]] | None,
    n1: int,
    n2: int,
    mean1: float,
    mean2: float,
    sd1: float,
    sd2: float,
) -> tuple[int, int, float, float, float, float] | None:
    if groups and len(groups) >= 2:
        keys = list(groups)[:2]
        a, b = groups[keys[0]], groups[keys[1]]
        if len(a) < 2 or len(b) < 2:
            return None
        return (
            len(a),
            len(b),
            statistics.fmean(a),
            statistics.fmean(b),
            statistics.stdev(a),
            statistics.stdev(b),
        )
    if n1 >= 2 and n2 >= 2 and sd1 > 0 and sd2 > 0:
        return n1, n2, mean1, mean2, sd1, sd2
    return None


def cliffs_delta(a: list[float], b: list[float], conf: float) -> dict[str, Any]:
    """Cliff's delta with the consistent (Cliff 1993) variance and a normal CI."""
    n1, n2 = len(a), len(b)
    dominance = [[(1 if x > y else (-1 if x < y else 0)) for y in b] for x in a]
    total = sum(sum(row) for row in dominance)
    delta = total / (n1 * n2)
    di = [sum(row) / n2 for row in dominance]
    dj = [sum(dominance[i][j] for i in range(n1)) / n1 for j in range(n2)]
    s_i = sum((v - delta) ** 2 for v in di)
    s_j = sum((v - delta) ** 2 for v in dj)
    s_ij = sum((dominance[i][j] - delta) ** 2 for i in range(n1) for j in range(n2))
    denom = n1 * n2 * (n1 - 1) * (n2 - 1)
    var = (n2 * n2 * s_i + n1 * n1 * s_j - s_ij) / denom if denom > 0 else 0.0
    se = math.sqrt(max(var, 0.0))
    z = norm_ppf(1.0 - (1.0 - conf) / 2.0)
    return {
        "estimate": delta,
        "se": se,
        "ci_low": max(-1.0, delta - z * se),
        "ci_high": min(1.0, delta + z * se),
        "method": "Cliff's delta, consistent variance estimator, normal-approximation CI",
        "accuracy": (
            "large-sample normal interval; near |delta| = 1 it can run past the "
            "[-1, 1] boundary and is truncated, which makes coverage approximate"
        ),
        "n": {"n1": n1, "n2": n2},
    }


def effect_size_core(
    kind: str,
    *,
    groups: dict[str, list[float]] | None = None,
    n1: int = 0,
    n2: int = 0,
    mean1: float = 0.0,
    mean2: float = 0.0,
    sd1: float = 0.0,
    sd2: float = 0.0,
    a: int = 0,
    b: int = 0,
    c: int = 0,
    d: int = 0,
    r: float = 0.0,
    pairs: list[tuple[float, float]] | None = None,
    anova: dict[str, float] | None = None,
    conf_level: float = 0.95,
    hedges_correction: bool = True,
) -> dict[str, Any]:
    """One effect size with an interval and the method that produced it."""
    conf = min(max(conf_level, 0.5), 0.9999)
    z = norm_ppf(1.0 - (1.0 - conf) / 2.0)

    if kind in ("cohens_d", "hedges_g", "glass_delta"):
        summary = _two_group_summary(groups, n1, n2, mean1, mean2, sd1, sd2)
        if summary is None:
            raise ValueError(
                "need two groups: either data (data_path + outcome + group, or values) "
                "or n1,n2,mean1,mean2,sd1,sd2"
            )
        na, nb, ma, mb, sa, sb = summary
        df = na + nb - 2.0
        if kind == "glass_delta":
            if sb <= 0:
                raise ValueError("glass_delta needs sd2 > 0 (the control group's sd)")
            est = (ma - mb) / sb
            se = math.sqrt((na + nb) / (na * nb) + est * est / (2.0 * (nb - 1)))
            return {
                "kind": kind,
                "estimate": est,
                "ci_low": est - z * se,
                "ci_high": est + z * se,
                "se": se,
                "n": {"n1": na, "n2": nb},
                "method": "Glass's delta on the control-group sd; normal-approximation CI",
                "accuracy": (
                    "the CI is a large-sample normal interval on an approximate SE; "
                    "it is not the exact noncentral-t interval"
                ),
            }
        sp = math.sqrt(((na - 1) * sa * sa + (nb - 1) * sb * sb) / df)
        est = (ma - mb) / sp
        t = est * math.sqrt(na * nb / (na + nb))
        low, high = _nct_ncp_ci(t, df, conf)
        scale = math.sqrt((na + nb) / (na * nb))
        ci_low, ci_high = low * scale, high * scale
        se = math.sqrt((na + nb) / (na * nb) + est * est / (2.0 * df))
        method = "Cohen's d on the pooled sd; CI by noncentral-t inversion"
        if kind == "hedges_g" or hedges_correction:
            j = _hedges_j(df)
            if kind == "hedges_g":
                est, ci_low, ci_high, se = est * j, ci_low * j, ci_high * j, se * j
                method = (
                    "Hedges's g (exact J = Gamma(df/2)/(sqrt(df/2) Gamma((df-1)/2))); "
                    "CI by noncentral-t inversion, then scaled by J"
                )
        return {
            "kind": kind,
            "estimate": est,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "se": se,
            "n": {"n1": na, "n2": nb},
            "method": method,
            "accuracy": (
                "noncentrality inverted to 1e-9 on a cdf held to rtol 1e-12; the "
                "interval assumes normal within-group data and equal variances"
            ),
        }

    if kind == "cohens_dz":
        values: list[float] = []
        if pairs:
            values = [x - y for x, y in pairs]
        elif groups and len(groups) >= 2:
            keys = list(groups)[:2]
            ga, gb = groups[keys[0]], groups[keys[1]]
            if len(ga) != len(gb):
                raise ValueError("cohens_dz needs paired data: the two columns must be equal length")
            values = [x - y for x, y in zip(ga, gb, strict=True)]
        elif groups and len(groups) == 1:
            values = list(next(iter(groups.values())))
        if len(values) < 2:
            raise ValueError(
                "cohens_dz needs the paired differences: values=[[x...],[y...]] or a "
                "single array of differences"
            )
        n = len(values)
        sd = statistics.stdev(values)
        if sd <= 0:
            raise ValueError("every difference is identical (sd = 0)")
        est = statistics.fmean(values) / sd
        t = est * math.sqrt(n)
        low, high = _nct_ncp_ci(t, n - 1.0, conf)
        scale = 1.0 / math.sqrt(n)
        return {
            "kind": kind,
            "estimate": est,
            "ci_low": low * scale,
            "ci_high": high * scale,
            "se": math.sqrt(1.0 / n + est * est / (2.0 * n)),
            "n": {"pairs": n},
            "method": "Cohen's dz = mean(diff)/sd(diff); CI by noncentral-t inversion",
            "accuracy": (
                "noncentrality inverted to 1e-9; dz is NOT comparable with a "
                "between-group d unless you also report the pre-post correlation"
            ),
        }

    if kind == "cliffs_delta":
        if not groups or len(groups) < 2:
            raise ValueError("cliffs_delta needs two groups of raw values")
        keys = list(groups)[:2]
        out = cliffs_delta(groups[keys[0]], groups[keys[1]], conf)
        out["kind"] = kind
        return out

    if kind == "pearson_r":
        n = 0
        est = 0.0
        if pairs and len(pairs) >= 4:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            est = statistics.correlation(xs, ys)
            n = len(pairs)
        elif r != 0.0 and n1 >= 4:
            est, n = float(r), int(n1)
        else:
            raise ValueError("pearson_r needs paired data (values=[[x,y],...]) or r= plus n1=")
        est = min(max(est, -0.999999), 0.999999)
        zr = math.atanh(est)
        se_z = 1.0 / math.sqrt(n - 3.0)
        return {
            "kind": kind,
            "estimate": est,
            "ci_low": math.tanh(zr - z * se_z),
            "ci_high": math.tanh(zr + z * se_z),
            "se": se_z,
            "n": {"pairs": n},
            "method": "Pearson r; CI by Fisher z transformation",
            "accuracy": (
                "Fisher z is exact only under bivariate normality; with heavy tails "
                "the coverage drifts — a bootstrap CI is the usual repair"
            ),
        }

    if kind in ("odds_ratio", "risk_ratio", "risk_difference", "nnt"):
        cells = [a, b, c, d]
        if min(cells) < 0 or sum(cells) == 0:
            raise ValueError(f"{kind} needs the 2x2 cells a,b,c,d (exposed/unexposed x event/no event)")
        adjusted = False
        aa, bb, cc, dd = (float(x) for x in cells)
        if 0 in cells:
            aa, bb, cc, dd = aa + 0.5, bb + 0.5, cc + 0.5, dd + 0.5
            adjusted = True
        n_exposed, n_control = aa + bb, cc + dd
        p1, p2 = aa / n_exposed, cc / n_control
        if kind == "odds_ratio":
            est = (aa * dd) / (bb * cc)
            se_log = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
            return {
                "kind": kind,
                "estimate": est,
                "ci_low": math.exp(math.log(est) - z * se_log),
                "ci_high": math.exp(math.log(est) + z * se_log),
                "se": se_log,
                "n": {"total": int(sum(cells))},
                "method": "odds ratio; Woolf log SE"
                + (" with a 0.5 continuity correction (a zero cell)" if adjusted else ""),
                "accuracy": (
                    "the log SE is a large-sample approximation; with any cell under "
                    "about 5 an exact conditional (Fisher) interval is the honest one"
                ),
            }
        if kind == "risk_ratio":
            est = p1 / p2
            se_log = math.sqrt(1 / aa - 1 / n_exposed + 1 / cc - 1 / n_control)
            return {
                "kind": kind,
                "estimate": est,
                "ci_low": math.exp(math.log(est) - z * se_log),
                "ci_high": math.exp(math.log(est) + z * se_log),
                "se": se_log,
                "n": {"total": int(sum(cells))},
                "method": "risk ratio; Katz log SE"
                + (" with a 0.5 continuity correction (a zero cell)" if adjusted else ""),
                "accuracy": "large-sample log interval; unreliable for very rare events",
            }
        rd = p1 - p2
        se_rd = math.sqrt(p1 * (1 - p1) / n_exposed + p2 * (1 - p2) / n_control)
        lo_rd, hi_rd = rd - z * se_rd, rd + z * se_rd
        if kind == "risk_difference":
            return {
                "kind": kind,
                "estimate": rd,
                "ci_low": lo_rd,
                "ci_high": hi_rd,
                "se": se_rd,
                "n": {"total": int(sum(cells))},
                "method": "risk difference; Wald normal interval",
                "accuracy": "Wald coverage is poor near 0 or 1 — Newcombe/Wilson is better there",
            }
        spans_null = lo_rd <= 0.0 <= hi_rd
        return {
            "kind": kind,
            "estimate": (1.0 / rd) if rd != 0 else float("inf"),
            "ci_low": (1.0 / hi_rd) if hi_rd != 0 else float("inf"),
            "ci_high": (1.0 / lo_rd) if lo_rd != 0 else float("inf"),
            "se": se_rd,
            "n": {"total": int(sum(cells))},
            "risk_difference": rd,
            "ci_spans_no_effect": spans_null,
            "method": "NNT = 1 / risk difference; interval from the Wald RD interval",
            "accuracy": (
                "when the RD interval contains zero the NNT interval is NOT a single "
                "range — it is two rays (number needed to treat and number needed to "
                "harm). Report the risk difference interval instead."
                if spans_null
                else "inherits the Wald RD approximation"
            ),
        }

    if kind == "cramers_v":
        cells = [a, b, c, d]
        if min(cells) < 0 or sum(cells) == 0:
            raise ValueError("cramers_v needs the 2x2 cells a,b,c,d")
        n_total = float(sum(cells))
        chi2 = n_total * (a * d - b * c) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))
        est = math.sqrt(chi2 / n_total)
        alpha = 1.0 - conf
        span = max(200.0, chi2 * 4.0 + 50.0)
        lam_low = _solve_ncp(lambda lam: ncchi2_cdf(chi2, 1.0, lam), 1.0 - alpha / 2.0, 0.0, span)
        lam_high = _solve_ncp(lambda lam: ncchi2_cdf(chi2, 1.0, lam), alpha / 2.0, 0.0, span)
        return {
            "kind": kind,
            "estimate": est,
            "ci_low": math.sqrt(max(0.0, lam_low) / n_total),
            "ci_high": math.sqrt(max(0.0, lam_high) / n_total),
            "se": None,
            "n": {"total": int(n_total)},
            "chi_square": chi2,
            "method": "Cramer's V (= phi for 2x2); CI by noncentral chi-square inversion",
            "accuracy": (
                "the noncentrality interval is exact for the chi-square reference "
                "distribution, which is itself asymptotic — poor with expected cells < 5"
            ),
        }

    if kind in ("eta_squared", "partial_eta_squared", "omega_squared"):
        f_stat = df1 = df2 = 0.0
        if anova:
            f_stat = float(anova.get("f", anova.get("F", 0.0)))
            df1 = float(anova.get("df1", 0.0))
            df2 = float(anova.get("df2", 0.0))
        elif groups and len(groups) >= 2:
            usable = {k: v for k, v in groups.items() if len(v) >= 2}
            if len(usable) < 2:
                raise ValueError("need at least two groups with n >= 2")
            total = [v for vals in usable.values() for v in vals]
            grand = statistics.fmean(total)
            k = len(usable)
            n = len(total)
            ss_between = sum(len(v) * (statistics.fmean(v) - grand) ** 2 for v in usable.values())
            ss_within = sum((x - statistics.fmean(v)) ** 2 for v in usable.values() for x in v)
            df1, df2 = k - 1.0, n - k
            if ss_within <= 0 or df2 <= 0:
                raise ValueError("within-group variance is zero")
            f_stat = (ss_between / df1) / (ss_within / df2)
        if f_stat <= 0 or df1 <= 0 or df2 <= 0:
            raise ValueError(
                f"{kind} needs group data, or values='{{\"f\": .., \"df1\": .., \"df2\": ..}}'"
            )
        n_total = df1 + df2 + 1.0
        eta = (f_stat * df1) / (f_stat * df1 + df2)
        alpha = 1.0 - conf
        span = max(400.0, f_stat * df1 * 6.0 + 100.0)
        lam_low = _solve_ncp(
            lambda lam: ncf_cdf(f_stat, df1, df2, lam), 1.0 - alpha / 2.0, 0.0, span
        )
        lam_high = _solve_ncp(lambda lam: ncf_cdf(f_stat, df1, df2, lam), alpha / 2.0, 0.0, span)
        est = eta
        method = "eta^2 from F; CI by noncentral F inversion (lambda -> lambda/(lambda+N))"
        extra = ""
        if kind == "omega_squared":
            est = (df1 * (f_stat - 1.0)) / (df1 * (f_stat - 1.0) + n_total)
            method = "omega^2 (less biased than eta^2); CI mapped from the noncentral F interval"
            extra = (
                " the omega^2 interval is the eta^2 noncentrality interval re-expressed, "
                "not an exact omega^2 interval"
            )
        elif kind == "partial_eta_squared":
            method = (
                "partial eta^2 from F; for a ONE-WAY design it equals eta^2. For a "
                "factorial design pass the effect's own F, df1 and df2."
            )
        return {
            "kind": kind,
            "estimate": est,
            "ci_low": max(0.0, lam_low) / (max(0.0, lam_low) + n_total),
            "ci_high": max(0.0, lam_high) / (max(0.0, lam_high) + n_total),
            "se": None,
            "n": {"total": int(n_total)},
            "f": f_stat,
            "df1": df1,
            "df2": df2,
            "method": method,
            "accuracy": (
                "noncentrality inverted to 1e-9 on a cdf held to rtol 1e-12;" + extra
            ),
        }

    raise ValueError(f"unknown kind: {kind}. Known: {', '.join(EFFECT_KINDS)}")


# ---------------------------------------------------------------------------
# manuscript log condenser
# ---------------------------------------------------------------------------

_TEX_FILE_RE = re.compile(r"\((?:\./|\.\\)?([A-Za-z0-9_./\\@+-]+\.(?:tex|sty|cls|bbl|aux))")
_LINE_RE = re.compile(r"^l\.(\d+)")
_UNDEF_CITE_RE = re.compile(r"Citation [`'\"]([^'\"`]+)['\"`] on page|Citation [`'\"]([^'\"`]+)['\"`] undefined")
_UNDEF_REF_RE = re.compile(r"Reference [`'\"]([^'\"`]+)['\"`] on page|Reference [`'\"]([^'\"`]+)['\"`] undefined")
_FILE_MISSING_RE = re.compile(r"File [`'\"]([^'\"`]+)['\"`] not found")
_OVERFULL_RE = re.compile(r"^(Over|Under)full \\[hv]box \((?:(\d+(?:\.\d+)?)pt too wide|.*?)\)")

_ERROR_HINTS = {
    "Undefined control sequence": "a macro is misspelled or its package is not loaded",
    "Missing $ inserted": "maths outside $...$ — often an underscore or ^ in text",
    "Runaway argument": "an unclosed brace or a blank line inside a short argument",
    "File ended while scanning": "an unclosed group or environment earlier in the file",
    "Emergency stop": "the run aborted on the error above it",
    "not found": "the file is missing or the path is wrong relative to the .tex",
}


def condense_tex_log(text: str, *, max_errors: int = 25) -> dict[str, Any]:
    """Reduce a TeX/biber log to the lines that mean something.

    Keeps ``! ...`` errors (with ``file:line`` from the enclosing file stack),
    undefined citation/reference warnings, missing files, and biber/biblatex
    ERROR/WARN lines. Overfull/underfull boxes are counted, not listed, unless
    an overfull box is worse than 10pt (then at most five of those are shown).
    """
    errors: list[dict[str, Any]] = []
    undefined_citations: list[str] = []
    undefined_references: list[str] = []
    stack: list[str] = []
    overfull = underfull = warnings = 0
    big_boxes: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    truncated_errors = 0

    def hint_for(message: str) -> str:
        for key, hint in _ERROR_HINTS.items():
            if key in message:
                return hint
        return ""

    def push(entry: dict[str, Any]) -> None:
        nonlocal truncated_errors
        if len(errors) < max_errors:
            errors.append(entry)
        else:
            truncated_errors += 1

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        for m in _TEX_FILE_RE.finditer(line):
            stack.append(m.group(1))
        closes = line.count(")")
        for _ in range(min(closes, len(stack))):
            stack.pop()
        current = stack[-1] if stack else ""

        if pending is not None:
            hit = _LINE_RE.match(line.strip())
            if hit:
                pending["line"] = int(hit.group(1))
                push(pending)
                pending = None
                continue
            if line.startswith("!") or not line.strip():
                push(pending)
                pending = None
            elif pending.get("message", "").endswith(("...", "-")) is False and len(
                pending.get("message", "")
            ) < 200:
                pending["message"] = (pending["message"] + " " + line.strip()).strip()

        if line.startswith("!"):
            message = line.lstrip("! ").strip()
            pending = {
                "severity": "error",
                "message": message,
                "file": current,
                "line": 0,
                "hint": hint_for(message),
            }
            continue

        box = _OVERFULL_RE.match(line)
        if box:
            if box.group(1) == "Over":
                overfull += 1
                size = float(box.group(2)) if box.group(2) else 0.0
                if size > 10.0 and len(big_boxes) < 5:
                    big_boxes.append({"severity": "layout", "message": line, "file": current})
            else:
                underfull += 1
            continue

        if "Citation" in line and "undefined" in line:
            mc = _UNDEF_CITE_RE.search(line)
            key = next((g for g in (mc.groups() if mc else ()) if g), "")
            if key and key not in undefined_citations:
                undefined_citations.append(key)
            warnings += 1
            continue
        if "Reference" in line and "undefined" in line:
            mr = _UNDEF_REF_RE.search(line)
            key = next((g for g in (mr.groups() if mr else ()) if g), "")
            if key and key not in undefined_references:
                undefined_references.append(key)
            warnings += 1
            continue
        missing = _FILE_MISSING_RE.search(line)
        if missing:
            push(
                {
                    "severity": "error",
                    "message": f"file not found: {missing.group(1)}",
                    "file": current,
                    "line": 0,
                    "hint": "check the path relative to the .tex, and the extension",
                }
            )
            continue
        if re.match(r"^\s*(ERROR|FATAL)\s*-\s", line) or line.startswith("BibTeX: Error"):
            push({"severity": "error", "message": line.strip(), "file": "biber/bibtex", "line": 0,
                  "hint": "the bibliography backend failed — check the .bib entry it names"})
            continue
        if re.match(r"^\s*WARN\s*-\s", line):
            warnings += 1
            continue
        if "LaTeX Warning" in line or "Package" in line and "Warning" in line:
            warnings += 1

    if pending is not None:
        push(pending)
    errors.extend(big_boxes)
    return {
        "errors": errors,
        "undefined_citations": undefined_citations,
        "undefined_references": undefined_references,
        "suppressed": {
            "overfull": overfull,
            "underfull": underfull,
            "warnings": warnings,
            "errors_over_cap": truncated_errors,
        },
    }


# ---------------------------------------------------------------------------
# subprocess plumbing (identical shape to bash_exec / agent_game_tools)
# ---------------------------------------------------------------------------


def _tail(text: str, cap: int = _TAIL_CHARS) -> str:
    text = text or ""
    if len(text) <= cap:
        return text
    return f"...[{len(text) - cap} chars cut]\n" + text[-cap:]


def _argv_text(argv: list[str]) -> str:
    return " ".join(f'"{a}"' if " " in a else a for a in argv)


def _write_roots(runtime: Any) -> list[Path]:
    try:
        return list(runtime.write_roots() or [])
    except Exception:
        return []


def _approval_block(runtime: Any, tool: str, command: str) -> str | None:
    """Same partner-trust gate bash_exec applies (ask mode -> APPROVAL_REQUIRED)."""
    try:
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id
    except Exception:
        return None
    reason = APPROVALS.needs_ask(command, tool_name=tool)
    sid = turn_session_id(runtime)
    if not reason or APPROVALS.is_approved(tool, command, session_id=sid):
        return None
    item = APPROVALS.create(tool_name=tool, command=command, reason=reason, session_id=sid)
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={reason}\n"
        f"command={command[:400]}\n"
        "Do not invent success. Tell the user this needs approval in the UI "
        f"(or /approve {item.id}). After they approve, retry {tool}."
    )


async def _sandbox_run(runtime: Any, argv: list[str], *, cwd: Path, timeout: float) -> Any:
    """Run argv through SubprocessSandbox exactly like bash_exec does."""
    from remedy.core.project_fingerprint import path_env_with_local_bins
    from remedy.execution.sandbox import SubprocessSandbox, allowed_paths_for_shell

    roots = _write_roots(runtime) or [cwd]
    sandbox = SubprocessSandbox(allowed_paths=allowed_paths_for_shell(roots, cwd))
    env = path_env_with_local_bins(cwd)
    return await sandbox.execute(argv, workdir=cwd, timeout_seconds=timeout, env=env)


def _which(name: str, cwd: Path) -> str:
    """Look a binary up on the PATH the sandbox will actually use."""
    try:
        from remedy.core.project_fingerprint import path_env_with_local_bins

        env = path_env_with_local_bins(cwd)
        return shutil.which(name, path=env.get("PATH")) or ""
    except Exception:
        return shutil.which(name) or ""


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value if value is not None else default)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# reporting checklists (structured data, transcribed from the published sources)
# ---------------------------------------------------------------------------
#
# PROVENANCE. The `requirement` strings below are SHORT PARAPHRASES written
# from the published checklists named in CHECKLIST_SOURCES — they are not the
# canonical wording and must not be quoted as such. Item numbering and section
# labels follow those sources. Anyone editing this table opens the source URL
# first; nothing here is written from memory. The tool always returns the
# source block so a reader can go and check.

CHECKLIST_SOURCES: dict[str, dict[str, str]] = {
    "consort": {
        "name": "CONSORT",
        "version": "2010",
        "url": "https://www.equator-network.org/reporting-guidelines/consort/",
        "citation": (
            "Schulz KF, Altman DG, Moher D. CONSORT 2010 Statement: updated guidelines "
            "for reporting parallel group randomised trials. BMJ 2010;340:c332."
        ),
        "applies_to": "randomised controlled trials",
        "wording": "paraphrased; canonical item wording is at the URL",
    },
    "prisma": {
        "name": "PRISMA",
        "version": "2020",
        "url": "https://www.prisma-statement.org/prisma-2020-checklist",
        "citation": (
            "Page MJ, et al. The PRISMA 2020 statement: an updated guideline for "
            "reporting systematic reviews. BMJ 2021;372:n71."
        ),
        "applies_to": "systematic reviews and meta-analyses",
        "wording": "paraphrased; canonical item wording is at the URL",
    },
    "strobe": {
        "name": "STROBE",
        "version": "v4 combined checklist (cohort, case-control, cross-sectional)",
        "url": "https://www.strobe-statement.org/checklists/",
        "citation": (
            "STROBE Statement - checklist of items that should be included in reports "
            "of observational studies; STROBE Initiative, www.strobe-statement.org."
        ),
        "applies_to": "observational studies",
        "wording": "paraphrased; canonical item wording is at the URL",
    },
    "arrive": {
        "name": "ARRIVE",
        "version": "2.0 (Essential 10 + Recommended Set)",
        "url": "https://arriveguidelines.org/arrive-guidelines",
        "citation": (
            "ARRIVE guidelines 2.0, as published with the Essential 10 and Recommended "
            "Set; see https://pmc.ncbi.nlm.nih.gov/articles/PMC7360023/"
        ),
        "applies_to": "research involving live animals",
        "wording": "paraphrased; canonical item wording is at the URL",
    },
    "mdar": {
        "name": "MDAR (Materials Design Analysis Reporting)",
        "version": "Checklist for Authors (2021)",
        "url": "https://www.pnas.org/doi/10.1073/pnas.2103238118",
        "citation": (
            "The MDAR (Materials Design Analysis Reporting) Framework for transparent "
            "reporting in the life sciences. PNAS 2021; doi:10.1073/pnas.2103238118."
        ),
        "applies_to": "life-science reproducibility (applied as a base layer)",
        "wording": "paraphrased; canonical item wording is at the URL",
    },
}


def _item(
    item_id: str,
    section: str,
    requirement: str,
    patterns: list[str],
    where: str,
    add: str,
    judgement: bool = False,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "section": section,
        "item": requirement,
        "patterns": patterns,
        "where_it_should_go": where,
        "what_to_add": add,
        "judgement": judgement,
    }


_CONSORT_ITEMS = [
    _item("1a", "Title and abstract", "Identify the study as randomised in the title.",
          [r"\brandomi[sz]ed\b.{0,40}\b(trial|study)\b", r"\btrial\b.{0,30}\brandomi[sz]ed\b"],
          "title", "Put the word randomised in the title."),
    _item("1b", "Title and abstract",
          "Structured abstract covering trial design, methods, results and conclusions.",
          [r"(?i)^\s*abstract\b", r"\\begin\{abstract\}"],
          "abstract", "Add a structured abstract with design, methods, results, conclusions."),
    _item("2a", "Introduction",
          "Scientific background and rationale for the trial.",
          [r"\brationale\b", r"\bbackground\b", r"\bunmet (clinical )?need\b"],
          "introduction", "State why this trial was needed."),
    _item("2b", "Introduction", "Specific objectives or hypotheses.",
          [r"\b(objectives?|hypothes[ei]s|aim(s|ed)? (of|to))\b", r"\bwe hypothesi[sz]ed\b"],
          "introduction, last paragraph", "State the objective or hypothesis explicitly."),
    _item("3a", "Methods - trial design",
          "Trial design (parallel, factorial, crossover) with the allocation ratio.",
          [r"\b(parallel[- ]group|factorial|cross-?over|cluster)\b.{0,30}\b(design|trial)\b",
           r"\ballocation ratio\b", r"\b1\s*:\s*1\b"],
          "methods, trial design", "Name the design and give the allocation ratio."),
    _item("3b", "Methods - trial design",
          "Important changes to methods after trial commencement, with reasons.",
          [r"\bprotocol (was )?(amend|chang|modif)", r"\bafter (the )?trial (commenc|start|began)",
           r"\bno changes were made to the (protocol|methods)\b"],
          "methods, trial design", "State any protocol changes and why, or say there were none.",
          judgement=True),
    _item("4a", "Methods - participants", "Eligibility criteria for participants.",
          [r"\b(inclusion|exclusion|eligibility) criteria\b", r"\bparticipants were eligible\b"],
          "methods, participants", "List inclusion and exclusion criteria."),
    _item("4b", "Methods - participants", "Settings and locations where the data were collected.",
          [r"\b(recruit(ed|ment)|enrolled) (at|in|from)\b", r"\b(single|multi)-?cent(re|er)\b",
           r"\bstudy (site|setting)s?\b"],
          "methods, participants", "Name the sites and the setting."),
    _item("5", "Methods - interventions",
          "Interventions for each group in enough detail to replicate, including how and when.",
          [r"\b(intervention|treatment) (group|arm)\b", r"\b(dose|dosage|administered|regimen)\b",
           r"\bcontrol (group|arm)\b"],
          "methods, interventions", "Describe each arm's intervention: what, dose, route, timing."),
    _item("6a", "Methods - outcomes",
          "Completely defined pre-specified primary and secondary outcomes and how they were assessed.",
          [r"\bprimary (outcome|endpoint)\b", r"\bsecondary (outcomes?|endpoints?)\b"],
          "methods, outcomes", "Define the primary outcome, its measure, and when it was assessed."),
    _item("6b", "Methods - outcomes", "Any changes to outcomes after the trial began, with reasons.",
          [r"\b(outcome|endpoint)s? (were|was) (changed|revised|added)\b",
           r"\bno changes to (the )?(outcomes|endpoints)\b"],
          "methods, outcomes", "State outcome changes and why, or say there were none.",
          judgement=True),
    _item("7a", "Methods - sample size", "How sample size was determined.",
          [r"\bsample size (was )?(calculat|determin|estimat)", r"\bpower (calculation|analysis)\b",
           r"\bto detect (a|an) .{0,40}(difference|effect)\b"],
          "methods, sample size", "Give the assumed effect, alpha, power and the resulting n."),
    _item("7b", "Methods - sample size",
          "Interim analyses and stopping guidelines, when applicable.",
          [r"\binterim analys[ei]s\b", r"\bstopping (rule|guideline|boundar)",
           r"\bdata (safety )?monitoring\b"],
          "methods, sample size", "Describe interim looks and stopping rules, or say there were none.",
          judgement=True),
    _item("8a", "Methods - randomisation",
          "Method used to generate the random allocation sequence.",
          [r"\brandom(isation|ization) (sequence|list|schedule)\b",
           r"\b(computer|software)[- ]generated random\b", r"\brandom number generator\b"],
          "methods, randomisation", "Say how the sequence was generated."),
    _item("8b", "Methods - randomisation",
          "Type of randomisation and any restriction such as blocking and block size.",
          [r"\bblock(ed|s)? randomi[sz]", r"\bstratifi(ed|cation)\b", r"\bblock size\b",
           r"\bpermuted blocks?\b"],
          "methods, randomisation", "State the randomisation type, blocking and stratification."),
    _item("9", "Methods - randomisation", "Allocation concealment mechanism.",
          [r"\ballocation concealment\b", r"\bsealed,? opaque\b", r"\bcentral(ised|ized)? allocation\b",
           r"\bconcealed until\b"],
          "methods, randomisation", "Describe how allocation was concealed until assignment."),
    _item("10", "Methods - randomisation",
          "Who generated the sequence, who enrolled participants, who assigned them.",
          [r"\bwho (generated|enrolled|assigned)\b", r"\benrolled participants\b",
           r"\bassigned participants to\b"],
          "methods, randomisation", "Name the roles: sequence generation, enrolment, assignment."),
    _item("11a", "Methods - blinding", "Who was blinded after assignment, and how.",
          [r"\b(double|single|triple)-?blind", r"\bblind(ed|ing)\b", r"\bmasked\b",
           r"\bopen[- ]label\b"],
          "methods, blinding", "Say who was blinded (participants, carers, assessors) and how."),
    _item("11b", "Methods - blinding", "Similarity of interventions, if relevant to blinding.",
          [r"\b(identical|matching|indistinguishable) (in )?(appearance|placebo|tablets?|capsules?)\b",
           r"\bplacebo\b.{0,40}\b(identical|matched)\b"],
          "methods, blinding", "Describe how the interventions were made indistinguishable.",
          judgement=True),
    _item("12a", "Methods - statistical methods",
          "Statistical methods used to compare groups for primary and secondary outcomes.",
          [r"\bstatistical analys[ei]s\b", r"\banalys(ed|is) using\b",
           r"\b(t-?test|ANOVA|logistic regression|Cox|mixed[- ]effects?|chi-?squared?)\b"],
          "methods, statistical analysis", "Name the model or test used for each outcome."),
    _item("12b", "Methods - statistical methods",
          "Methods for additional analyses, such as subgroup and adjusted analyses.",
          [r"\bsubgroup analys[ei]s\b", r"\badjusted analys[ei]s\b", r"\bsensitivity analys[ei]s\b",
           r"\bper-?protocol\b"],
          "methods, statistical analysis", "Describe subgroup and adjusted analyses and whether "
          "they were pre-specified."),
    _item("13a", "Results - participant flow",
          "Numbers randomly assigned, receiving intended treatment, and analysed for the primary outcome.",
          [r"\b(CONSORT )?flow (diagram|chart)\b", r"\bwere randomly assigned\b",
           r"\bparticipants? (were )?(randomi[sz]ed|allocated)\b"],
          "results, participant flow (a flow diagram)",
          "Add a CONSORT flow diagram with the numbers at each stage."),
    _item("13b", "Results - participant flow",
          "Losses and exclusions after randomisation, with reasons.",
          [r"\blost to follow-?up\b", r"\bwithdrew\b", r"\bexcluded after randomi[sz]ation\b",
           r"\bdiscontinued\b"],
          "results, participant flow", "Report losses and exclusions per arm with reasons."),
    _item("14a", "Results - recruitment", "Dates defining recruitment and follow-up periods.",
          [r"\b(recruit(ed|ment)|enrol(l)?ment) (between|from)\b.{0,40}\d{4}",
           r"\bfollow-?up (period|until|through)\b", r"\b(19|20)\d{2}\b.{0,20}\bto\b.{0,20}\b(19|20)\d{2}\b"],
          "results, recruitment", "Give recruitment start/end and follow-up dates."),
    _item("14b", "Results - recruitment", "Why the trial ended or was stopped.",
          [r"\btrial (was )?(stopped|terminated|halted|ended)\b", r"\breached the target sample size\b"],
          "results, recruitment", "Say why recruitment ended.", judgement=True),
    _item("15", "Results - baseline data",
          "A table of baseline demographic and clinical characteristics for each group.",
          [r"\bbaseline characteristics\b", r"\bTable\s*1\b", r"\bbaseline (demographics?|data)\b"],
          "results, Table 1", "Add a baseline characteristics table by arm."),
    _item("16", "Results - numbers analysed",
          "Number analysed in each group and whether the analysis was by original assigned groups.",
          [r"\bintention[- ]to[- ]treat\b", r"\bITT\b", r"\bas randomi[sz]ed\b",
           r"\bnumber(s)? analys(ed|is)\b"],
          "results", "State the denominator per arm and whether analysis was ITT."),
    _item("17a", "Results - outcomes and estimation",
          "For each outcome, results per group, the effect size and its precision.",
          [r"\b95%\s*(confidence interval|CI)\b", r"\bconfidence interval\b",
           r"\b(hazard|odds|risk) ratio\b.{0,40}\bCI\b"],
          "results", "Report the effect estimate with a confidence interval, not just a p-value."),
    _item("17b", "Results - outcomes and estimation",
          "For binary outcomes, both absolute and relative effect sizes are recommended.",
          [r"\babsolute risk (difference|reduction)\b", r"\brisk difference\b",
           r"\bnumber needed to treat\b"],
          "results", "Report the absolute risk difference alongside the relative effect."),
    _item("18", "Results - ancillary analyses",
          "All other analyses performed, distinguishing pre-specified from exploratory.",
          [r"\bexplorator(y|ily)\b", r"\bpre-?specified\b", r"\bpost[- ]hoc\b"],
          "results", "Label each extra analysis pre-specified or exploratory."),
    _item("19", "Results - harms",
          "All important harms or unintended effects in each group.",
          [r"\badverse events?\b", r"\bserious adverse\b", r"\bside[- ]effects?\b", r"\bharms?\b",
           r"\btoxicit(y|ies)\b"],
          "results, harms", "Report adverse events per arm, including serious ones."),
    _item("20", "Discussion", "Trial limitations, sources of potential bias, imprecision, multiplicity.",
          [r"\blimitations?\b", r"\bpotential bias\b", r"\bmultiplicity\b"],
          "discussion", "Add a limitations paragraph naming bias, imprecision and multiplicity.",
          judgement=True),
    _item("21", "Discussion", "Generalisability (external validity, applicability) of the findings.",
          [r"\bgenerali[sz]ab", r"\bexternal validity\b", r"\bapplicab(le|ility)\b"],
          "discussion", "Say to which populations and settings the result applies.", judgement=True),
    _item("22", "Discussion",
          "Interpretation consistent with results, balancing benefits and harms, with other evidence.",
          [r"\binterpret(ation|ed)\b", r"\bin (the )?context of\b", r"\bbenefits? and harms?\b",
           r"\bconsistent with (previous|prior)\b"],
          "discussion", "Interpret the result against benefits, harms and the prior evidence.",
          judgement=True),
    _item("23", "Other information", "Registration number and name of the trial registry.",
          [r"\bNCT\d{8}\b", r"\bISRCTN\d+\b", r"\bClinicalTrials\.gov\b", r"\btrial registration\b",
           r"\bregistr(y|ation) number\b"],
          "end of abstract and methods", "Add the registry name and registration number."),
    _item("24", "Other information", "Where the full trial protocol can be accessed.",
          [r"\b(full )?(trial )?protocol (is )?(available|can be (accessed|found))\b",
           r"\bsupplementary (protocol|appendix)\b", r"\bprotocol (doi|DOI)\b"],
          "other information", "Say where the protocol is published or deposited."),
    _item("25", "Other information", "Sources of funding and other support, and the role of funders.",
          [r"\bfund(ing|ed by|er)\b", r"\bgrant (number|no\.?)\b", r"\bsupported by\b",
           r"\bthe funders? had no role\b"],
          "other information", "Name the funders, grant numbers, and their role."),
]

_PRISMA_ITEMS = [
    _item("1", "Title", "Identify the report as a systematic review.",
          [r"\bsystematic review\b", r"\bmeta-?analysis\b"],
          "title", "Put 'systematic review' (and 'meta-analysis' if applicable) in the title."),
    _item("2", "Abstract", "A structured abstract following PRISMA 2020 for Abstracts.",
          [r"(?i)^\s*abstract\b", r"\\begin\{abstract\}"],
          "abstract", "Use the PRISMA 2020 for Abstracts structure."),
    _item("3", "Introduction", "Rationale for the review in the context of existing knowledge.",
          [r"\brationale\b", r"\bexisting (reviews?|evidence)\b", r"\bgap in (the )?(evidence|literature)\b"],
          "introduction", "Say why this review is needed given what already exists."),
    _item("4", "Introduction", "Explicit statement of the objectives or questions addressed.",
          [r"\b(objectives?|research questions?|we aimed to)\b", r"\bPICO\b"],
          "introduction", "State the review question, ideally in PICO form."),
    _item("5", "Methods", "Inclusion and exclusion criteria and how studies were grouped.",
          [r"\b(eligibility|inclusion|exclusion) criteria\b", r"\bstudies were (included|eligible) if\b"],
          "methods, eligibility criteria", "List eligibility criteria and the grouping for synthesis."),
    _item("6", "Methods", "All databases, registers and other sources searched, with the last search date.",
          [r"\b(PubMed|MEDLINE|Embase|Scopus|Web of Science|CINAHL|Cochrane|PsycINFO)\b",
           r"\bsearched? (up to|through|until)\b", r"\blast search(ed)?\b"],
          "methods, information sources", "Name every source searched and the date of the last search."),
    _item("7", "Methods", "Full search strategies for all databases, registers and websites.",
          [r"\bsearch (strategy|string|terms)\b", r"\bMeSH\b",
           r"\b(supplement(ary)?|appendix)\b.{0,40}\bsearch\b"],
          "methods, search strategy (usually a supplement)",
          "Include the full search string for each database."),
    _item("8", "Methods", "Selection process: how many reviewers screened, independently or not.",
          [r"\btwo (reviewers|authors) independently\b", r"\bscreen(ed|ing)\b",
           r"\btitle and abstract screening\b", r"\bdisagreements? were resolved\b"],
          "methods, selection process", "Say how many reviewers screened and how conflicts were resolved."),
    _item("9", "Methods", "Data collection process, including how many reviewers extracted data.",
          [r"\bdata (were )?extract(ed|ion)\b", r"\bextraction form\b",
           r"\bindependently extracted\b"],
          "methods, data collection", "Describe extraction and any duplication or verification."),
    _item("10a", "Methods", "All outcomes sought and which results were collected for each.",
          [r"\boutcomes? (sought|of interest)\b", r"\bprimary outcome\b", r"\bdata items?\b"],
          "methods, data items", "List the outcomes sought and the results collected."),
    _item("10b", "Methods", "All other variables collected and assumptions about missing information.",
          [r"\bmissing data\b", r"\bstudy characteristics (were )?(collected|extracted)\b",
           r"\bassumptions about\b"],
          "methods, data items", "List other variables and the assumptions used for missing data."),
    _item("11", "Methods", "Methods for assessing risk of bias in the included studies.",
          [r"\brisk of bias\b", r"\bRoB ?2\b", r"\bROBINS-?I\b", r"\bNewcastle-?Ottawa\b",
           r"\bquality assessment\b"],
          "methods, risk of bias", "Name the tool and how many assessors used it."),
    _item("12", "Methods", "Effect measures used for each outcome.",
          [r"\b(risk|odds|hazard) ratio\b", r"\bmean difference\b", r"\bstandardi[sz]ed mean difference\b",
           r"\beffect measures?\b"],
          "methods, effect measures", "State the effect measure used for each outcome."),
    _item("13a", "Methods", "How studies were assigned to each synthesis.",
          [r"\beligible for (the )?synthesis\b", r"\bgrouped (studies )?by\b",
           r"\bstudies were combined\b"],
          "methods, synthesis", "Describe how studies were grouped into syntheses."),
    _item("13b", "Methods", "Data preparation before synthesis (conversions, missing summary statistics).",
          [r"\bconverted\b.{0,40}\b(to|into)\b", r"\bimputed\b", r"\bstandard deviations? were\b"],
          "methods, synthesis", "Say how data were converted or imputed for synthesis."),
    _item("13c", "Methods", "Methods used to tabulate or visually display results.",
          [r"\bforest plot\b", r"\bsummary table\b", r"\btabulat(ed|ion)\b"],
          "methods, synthesis", "Say how results are displayed (forest plots, tables)."),
    _item("13d", "Methods", "Synthesis methods, including the meta-analysis model and heterogeneity.",
          [r"\b(random|fixed)[- ]effects?\b", r"\bDerSimonian\b", r"\bI\^?2\b|\bI2 statistic\b",
           r"\btau\^?2\b", r"\bnarrative synthesis\b"],
          "methods, synthesis", "Name the model, the heterogeneity statistic and the software."),
    _item("13e", "Methods", "Methods used to explore causes of heterogeneity.",
          [r"\bsubgroup analys[ei]s\b", r"\bmeta-?regression\b", r"\bheterogeneity was explored\b"],
          "methods, synthesis", "Describe subgroup analyses or meta-regression."),
    _item("13f", "Methods", "Sensitivity analyses assessing robustness of the synthesis.",
          [r"\bsensitivity analys[ei]s\b", r"\bleave-?one-?out\b", r"\brobustness\b"],
          "methods, synthesis", "Describe the sensitivity analyses."),
    _item("14", "Methods", "Methods to assess risk of bias due to missing results (reporting bias).",
          [r"\bpublication bias\b", r"\bfunnel plot\b", r"\bEgger.?s test\b", r"\breporting bias\b"],
          "methods, reporting bias", "Say how reporting/publication bias was assessed."),
    _item("15", "Methods", "Methods used to assess certainty in the body of evidence.",
          [r"\bGRADE\b", r"\bcertainty of (the )?evidence\b", r"\bconfidence in (the )?estimates?\b"],
          "methods, certainty assessment", "Name the certainty framework (e.g. GRADE)."),
    _item("16a", "Results", "Numbers screened, assessed and included, ideally with a flow diagram.",
          [r"\bflow diagram\b", r"\brecords? (were )?(identified|screened)\b",
           r"\b\d+ (records|studies) (were )?(identified|screened|included)\b"],
          "results, study selection", "Add the PRISMA flow diagram with counts at each stage."),
    _item("16b", "Results", "Studies that appeared to meet criteria but were excluded, with reasons.",
          [r"\bexcluded (studies|with reasons)\b", r"\breasons? for exclusion\b",
           r"\bfull-?text (articles )?excluded\b"],
          "results / supplement", "List excluded full texts with the reason for each."),
    _item("17", "Results", "Characteristics of each included study.",
          [r"\bcharacteristics of (the )?included studies\b", r"\bstudy characteristics\b",
           r"\bTable\s*1\b"],
          "results, Table 1", "Add a table of included-study characteristics."),
    _item("18", "Results", "Risk of bias assessments for each included study.",
          [r"\brisk of bias (assessment|results|summary)\b", r"\btraffic light plot\b",
           r"\b(low|high|some concerns) risk of bias\b"],
          "results", "Report the per-study risk-of-bias judgements."),
    _item("19", "Results", "For each study, summary statistics and effect estimates.",
          [r"\beffect estimates?\b", r"\bsummary statistics\b", r"\bper-?study (results|estimates)\b"],
          "results", "Report each study's estimate with its precision."),
    _item("20a", "Results", "For each synthesis, characteristics and risk of bias of contributing studies.",
          [r"\bstudies contributing to\b", r"\bincluded in (the|each) (meta-?analysis|synthesis)\b"],
          "results", "Summarise which studies fed each synthesis and their bias profile."),
    _item("20b", "Results", "Results of each synthesis with summary estimate, precision and heterogeneity.",
          [r"\bpooled (estimate|effect|OR|RR|SMD)\b", r"\bsummary (estimate|effect)\b",
           r"\bI\^?2\s*=\s*\d", r"\bheterogeneity\b"],
          "results", "Report the pooled estimate, CI and heterogeneity."),
    _item("20c", "Results", "Results of investigations of possible causes of heterogeneity.",
          [r"\bsubgroup (results|differences)\b", r"\bmeta-?regression\b"],
          "results", "Report subgroup / meta-regression findings."),
    _item("20d", "Results", "Results of all sensitivity analyses.",
          [r"\bsensitivity analys[ei]s (showed|indicated|results)\b", r"\bremained (robust|unchanged)\b"],
          "results", "Report what the sensitivity analyses changed."),
    _item("21", "Results", "Assessments of risk of bias due to missing results.",
          [r"\bfunnel plot\b", r"\bEgger\b", r"\bpublication bias (was|were)\b",
           r"\basymmetry\b"],
          "results", "Report the reporting-bias assessment."),
    _item("22", "Results", "Assessments of certainty of evidence for each outcome.",
          [r"\bGRADE\b", r"\b(moderate|low|very low|high) certainty\b",
           r"\bsummary of findings\b"],
          "results", "Add a summary-of-findings table with certainty ratings."),
    _item("23a", "Discussion", "Interpretation of the results in the context of other evidence.",
          [r"\bour (findings|results) (are )?(consistent|in line) with\b", r"\bin the context of\b"],
          "discussion", "Interpret the synthesis against the wider evidence.", judgement=True),
    _item("23b", "Discussion", "Limitations of the evidence included in the review.",
          [r"\blimitations? of the (evidence|included studies)\b", r"\bquality of the evidence\b"],
          "discussion", "State the limitations of the evidence base.", judgement=True),
    _item("23c", "Discussion", "Limitations of the review processes used.",
          [r"\blimitations? of (this|the) review\b", r"\bwe (may have|might have) missed\b",
           r"\bsearch was (restricted|limited)\b"],
          "discussion", "State the limitations of the review process itself.", judgement=True),
    _item("23d", "Discussion", "Implications for practice, policy and future research.",
          [r"\bimplications for (practice|policy|research)\b", r"\bfuture research\b"],
          "discussion", "Say what should change and what should be studied next.", judgement=True),
    _item("24a", "Other information", "Registration details, or a statement that the review was not registered.",
          [r"\bPROSPERO\b", r"\bCRD\d{6,}\b", r"\bregistered (in|with|on)\b",
           r"\bnot registered\b"],
          "other information", "Give the registration record or say it was not registered."),
    _item("24b", "Other information", "Where the review protocol can be accessed.",
          [r"\bprotocol (is )?(available|published|registered)\b", r"\bprotocol (doi|DOI)\b"],
          "other information", "Point to the protocol."),
    _item("24c", "Other information", "Amendments to the registered information, with reasons.",
          [r"\bamendments? to the protocol\b", r"\bdeviat(ed|ion)s? from the protocol\b",
           r"\bno amendments\b"],
          "other information", "List protocol amendments or say there were none.", judgement=True),
    _item("25", "Other information", "Sources of financial or non-financial support and the funders' role.",
          [r"\bfund(ing|ed by|er)\b", r"\bno funding\b", r"\bsupported by\b"],
          "other information", "Name funders and their role."),
    _item("26", "Other information", "Competing interests of the review authors.",
          [r"\bcompeting interests?\b", r"\bconflicts? of interest\b", r"\bdeclare no\b"],
          "other information", "Add a competing-interests statement."),
    _item("27", "Other information",
          "Which materials are publicly available (data, code, extraction forms) and where.",
          [r"\bdata (availability|are available)\b", r"\bcode (is )?available\b",
           r"\b(OSF|Zenodo|Dryad|GitHub)\b", r"\bextraction (forms?|template)\b"],
          "other information", "Say where the data, code and forms live."),
]

_STROBE_ITEMS = [
    _item("1a", "Title and abstract", "Indicate the study design with a common term in title or abstract.",
          [r"\b(cohort|case-?control|cross-?sectional|longitudinal|prospective|retrospective)\b"],
          "title / abstract", "Name the design (cohort, case-control, cross-sectional) up front."),
    _item("1b", "Title and abstract", "An informative, balanced abstract of what was done and found.",
          [r"(?i)^\s*abstract\b", r"\\begin\{abstract\}"],
          "abstract", "Add a balanced structured abstract."),
    _item("2", "Introduction", "Scientific background and rationale for the investigation.",
          [r"\brationale\b", r"\bbackground\b", r"\bprevious studies\b"],
          "introduction", "State the background and why the study was done."),
    _item("3", "Introduction", "State specific objectives, including any pre-specified hypotheses.",
          [r"\bobjectives?\b", r"\bhypothes[ei]s\b", r"\bwe aimed to\b"],
          "introduction", "State the objectives and any pre-specified hypotheses."),
    _item("4", "Methods", "Present key elements of the study design early in the paper.",
          [r"\bstudy design\b", r"\bwe conducted a (prospective|retrospective|population-based)\b",
           r"\b(cohort|case-?control|cross-?sectional) study\b"],
          "methods, first paragraph", "Name the design in the first methods paragraph."),
    _item("5", "Methods", "Setting, locations and relevant dates: recruitment, exposure, follow-up, collection.",
          [r"\bbetween\b.{0,30}\b(19|20)\d{2}\b", r"\bdata (were )?collected\b.{0,30}\b(19|20)\d{2}",
           r"\bfollow-?up (period|from|until)\b", r"\bstudy (setting|site)s?\b"],
          "methods, setting", "Give the setting, locations and the key dates."),
    _item("6a", "Methods - participants",
          "Eligibility criteria and the sources and methods of selection (and follow-up, for cohorts).",
          [r"\b(eligibility|inclusion|exclusion) criteria\b", r"\bparticipants were (selected|recruited)\b",
           r"\bcase ascertainment\b", r"\bcontrol selection\b"],
          "methods, participants", "State eligibility, how participants were selected and followed."),
    _item("6b", "Methods - participants",
          "For matched studies, matching criteria and the number matched per case or exposure group.",
          [r"\bmatch(ed|ing) (criteria|on|for)\b", r"\b(\d+)\s*:\s*1 matching\b",
           r"\bcontrols? per case\b"],
          "methods, participants", "State matching criteria and the matching ratio, if matched.",
          judgement=True),
    _item("7", "Methods - variables",
          "Define outcomes, exposures, predictors, confounders and effect modifiers; give diagnostic criteria.",
          [r"\bconfounder", r"\beffect modif", r"\bexposure (was )?defined\b",
           r"\boutcome (was )?defined\b", r"\bcovariates?\b"],
          "methods, variables", "Define every variable, including confounders and modifiers."),
    _item("8", "Methods - data sources",
          "For each variable, the data source and details of the measurement method.",
          [r"\bmeasured using\b", r"\bdata (were )?obtained from\b", r"\bregistry\b",
           r"\bquestionnaire\b", r"\bmedical records?\b"],
          "methods, data sources", "Give the source and measurement method per variable."),
    _item("9", "Methods - bias", "Describe efforts to address potential sources of bias.",
          [r"\b(selection|information|recall|measurement) bias\b", r"\bto (minimi[sz]e|address) bias\b"],
          "methods, bias", "Say what was done about each anticipated bias.", judgement=True),
    _item("10", "Methods - study size", "Explain how the study size was arrived at.",
          [r"\bstudy size\b", r"\bsample size\b", r"\bpower (calculation|analysis)\b",
           r"\ball (available|eligible) (participants|cases)\b"],
          "methods, study size", "Say how the study size was determined (or that it was a convenience set)."),
    _item("11", "Methods - quantitative variables",
          "How quantitative variables were handled, and which groupings were chosen and why.",
          [r"\bcategori[sz]ed\b", r"\bquartiles?\b", r"\btertiles?\b", r"\bas a continuous variable\b",
           r"\bcut-?offs?\b"],
          "methods, statistical analysis", "State whether variables were categorised and why."),
    _item("12a", "Methods - statistical methods",
          "All statistical methods, including those used to control for confounding.",
          [r"\badjusted for\b", r"\bmultivariable (model|regression)\b", r"\bstatistical analys[ei]s\b"],
          "methods, statistical analysis", "Name the models and the confounding control."),
    _item("12b", "Methods - statistical methods", "Methods used to examine subgroups and interactions.",
          [r"\bsubgroup\b", r"\binteraction (term|test)\b", r"\beffect modification\b"],
          "methods, statistical analysis", "Describe subgroup and interaction analyses."),
    _item("12c", "Methods - statistical methods", "How missing data were addressed.",
          [r"\bmissing data\b", r"\bmultiple imputation\b", r"\bcomplete[- ]case\b",
           r"\bno missing data\b"],
          "methods, statistical analysis", "State how missing data were handled."),
    _item("12d", "Methods - statistical methods",
          "Design-specific handling: loss to follow-up, matching, or the sampling strategy.",
          [r"\blost to follow-?up\b", r"\bcensor(ed|ing)\b", r"\bsampling (weights?|strategy)\b",
           r"\bmatched analysis\b"],
          "methods, statistical analysis",
          "Explain loss to follow-up (cohort), matching (case-control) or weighting (cross-sectional).",
          judgement=True),
    _item("12e", "Methods - statistical methods", "Describe any sensitivity analyses.",
          [r"\bsensitivity analys[ei]s\b", r"\bE-?value\b"],
          "methods, statistical analysis", "Describe the sensitivity analyses."),
    _item("13", "Results - participants",
          "Numbers at each stage, reasons for non-participation, and ideally a flow diagram.",
          [r"\b\d+ (participants|individuals|patients) (were )?(included|eligible|enrolled)\b",
           r"\bflow (diagram|chart)\b", r"\bnon-?participation\b", r"\bexcluded because\b"],
          "results, participants", "Report the counts at each stage and why people dropped out."),
    _item("14", "Results - descriptive data",
          "Participant characteristics, information on exposures and confounders, and missingness per variable.",
          [r"\bbaseline characteristics\b", r"\bTable\s*1\b",
           r"\bmissing (values|data) for\b", r"\bdemographics?\b"],
          "results, Table 1", "Add a descriptive table including missingness per variable."),
    _item("15", "Results - outcome data",
          "Report numbers of outcome events or summary measures (over time, for cohorts).",
          [r"\b\d+ (events|cases|deaths|outcomes)\b", r"\bincidence (rate|of)\b",
           r"\bperson-?(years|time)\b"],
          "results", "Report event counts or summary measures."),
    _item("16a", "Results - main results",
          "Unadjusted and confounder-adjusted estimates with precision; say which confounders and why.",
          [r"\bunadjusted\b", r"\badjusted (odds|hazard|risk) ratio\b", r"\b95% (CI|confidence interval)\b"],
          "results", "Report unadjusted and adjusted estimates with CIs and name the adjustment set."),
    _item("16b", "Results - main results", "Report category boundaries when continuous variables were categorised.",
          [r"\bquartiles?\b.{0,40}\b(range|cut)", r"\bcategory (boundaries|cut-?points)\b",
           r"\b(<|>|>=|<=)\s*\d+(\.\d+)?\s*(mg|kg|years|mmHg)"],
          "results", "Give the boundaries of each category.", judgement=True),
    _item("16c", "Results - main results",
          "If relevant, translate relative risk into absolute risk for a meaningful time period.",
          [r"\babsolute risk\b", r"\brisk difference\b", r"\bper 1,?000 (person-?years|people)\b"],
          "results", "Add an absolute-risk translation.", judgement=True),
    _item("17", "Results - other analyses",
          "Other analyses done: subgroups, interactions and sensitivity analyses.",
          [r"\bsubgroup analys[ei]s\b", r"\bsensitivity analys[ei]s\b", r"\binteraction\b"],
          "results", "Report the other analyses that were run."),
    _item("18", "Discussion", "Summarise key results with reference to the study objectives.",
          [r"\bin (this|our) study,? we (found|observed)\b", r"\bkey (results|findings)\b"],
          "discussion, first paragraph", "Open the discussion with the key result.", judgement=True),
    _item("19", "Discussion",
          "Limitations, sources of potential bias or imprecision, and the direction and magnitude of bias.",
          [r"\blimitations?\b", r"\bresidual confounding\b", r"\bwould bias\b.{0,30}\btoward\b"],
          "discussion", "Discuss limitations including the likely direction of each bias.",
          judgement=True),
    _item("20", "Discussion",
          "A cautious overall interpretation considering objectives, limitations, multiplicity and other evidence.",
          [r"\bcaution\b", r"\bmultiplicity\b", r"\bconsistent with (previous|other) studies\b",
           r"\bshould be interpreted\b"],
          "discussion", "Interpret cautiously against limitations and multiplicity.", judgement=True),
    _item("21", "Discussion", "Discuss the generalisability (external validity) of the results.",
          [r"\bgenerali[sz]ab", r"\bexternal validity\b", r"\bapplicab(le|ility)\b"],
          "discussion", "Say who these results apply to.", judgement=True),
    _item("22", "Other information",
          "The source of funding and the role of the funders, including for any original study.",
          [r"\bfund(ing|ed by|er)\b", r"\bgrant (number|no\.?)\b", r"\bthe funders? had no role\b"],
          "other information", "Name funders, grants and their role."),
]

_ARRIVE_ITEMS = [
    _item("1", "Essential 10 - study design",
          "Groups compared including controls, and the experimental unit.",
          [r"\bexperimental unit\b", r"\bcontrol group\b", r"\bsham\b", r"\bvehicle control\b",
           r"\bgroups? (were|of) (compared|animals)\b"],
          "methods, study design", "Name each group, its control, and what the experimental unit is."),
    _item("2", "Essential 10 - sample size",
          "The exact number of experimental units per group and how sample size was decided.",
          [r"\bn\s*=\s*\d+\b", r"\bsample size (was )?(calculat|determin|estimat)",
           r"\bpower (calculation|analysis)\b", r"\banimals per group\b"],
          "methods, sample size", "Give n per group and how it was chosen."),
    _item("3", "Essential 10 - inclusion and exclusion criteria",
          "Criteria for including and excluding animals and data points, and whether set in advance.",
          [r"\b(inclusion|exclusion) criteria\b", r"\banimals were excluded\b",
           r"\bdata points? (were )?excluded\b", r"\bpre-?(defined|specified)\b"],
          "methods", "State exclusion criteria and whether they were pre-defined."),
    _item("4", "Essential 10 - randomisation",
          "Whether and how animals were allocated at random, and how confounders were controlled.",
          [r"\brandom(ly|i[sz]ed|isation|ization)\b", r"\ballocat(ed|ion)\b",
           r"\bcage position\b", r"\border of treatment\b"],
          "methods, randomisation", "Say how animals were randomised and how order/position was handled."),
    _item("5", "Essential 10 - blinding",
          "Who was aware of group allocation during allocation, conduct, assessment and analysis.",
          [r"\bblind(ed|ing)\b", r"\bmasked\b", r"\bunaware of (the )?(group|treatment)\b"],
          "methods, blinding", "State who was blinded at each stage."),
    _item("6", "Essential 10 - outcome measures",
          "Precisely defined outcome measures, with the primary outcome identified.",
          [r"\bprimary outcome\b", r"\boutcome measures?\b", r"\bendpoints?\b"],
          "methods, outcomes", "Define the outcomes and name the primary one."),
    _item("7", "Essential 10 - statistical methods",
          "Statistical methods, the unit of analysis, and how assumptions were assessed.",
          [r"\bstatistical analys[ei]s\b", r"\b(ANOVA|t-?test|Mann-?Whitney|Kruskal)\b",
           r"\bnormality\b", r"\bunit of analysis\b"],
          "methods, statistics", "Name the test, the unit of analysis and the assumption checks."),
    _item("8", "Essential 10 - experimental animals",
          "Species, strain, sex, age, weight, health status and genetic modification.",
          [r"\b(C57BL/6|BALB/c|Sprague-?Dawley|Wistar|mice|mouse|rats?|zebrafish|Drosophila)\b",
           r"\b(male|female)\b.{0,30}\b(mice|rats?|animals)\b", r"\bstrain\b", r"\bweeks? old\b"],
          "methods, animals", "Give species, strain, sex, age, weight and health status."),
    _item("9", "Essential 10 - experimental procedures",
          "What was done to each group: how, when, where and why, in replicable detail.",
          [r"\banaesthe(sia|tised|tized)\b", r"\banalgesi", r"\bdose\b", r"\b(injected|administered)\b",
           r"\beuthani[sz]ed\b"],
          "methods, procedures", "Describe each procedure including anaesthesia and analgesia."),
    _item("10", "Essential 10 - results",
          "Summary statistics with a measure of variability, and effect sizes with confidence intervals.",
          [r"\bmean\b.{0,20}\b(SD|SEM|standard (deviation|error))\b", r"\b95% (CI|confidence interval)\b",
           r"\beffect size\b"],
          "results", "Report variability and effect sizes with intervals, not only p-values."),
    _item("11", "Recommended - abstract",
          "Abstract with objectives, species, key methods, principal findings and conclusions.",
          [r"(?i)^\s*abstract\b", r"\\begin\{abstract\}"],
          "abstract", "Add an abstract naming the species and the objective."),
    _item("12", "Recommended - background",
          "Scientific context, rationale, and the relevance of the animal model used.",
          [r"\brationale\b", r"\bbackground\b", r"\bmodel of\b", r"\brelevance to human\b"],
          "introduction", "Explain the scientific context and why this model."),
    _item("13", "Recommended - objectives", "Clear objectives and the hypotheses being tested.",
          [r"\bobjectives?\b", r"\bhypothes[ei]s\b", r"\bwe aimed to\b"],
          "introduction", "State the objectives and hypotheses."),
    _item("14", "Recommended - ethical statement",
          "The authority that approved the animal work, with licence or protocol numbers.",
          [r"\bIACUC\b", r"\bethics (committee|approval)\b", r"\banimal (welfare|care) committee\b",
           r"\bHome Office\b", r"\bprotocol (number|no\.?)\b", r"\bproject licence\b"],
          "methods, ethics statement", "Name the approving body and the protocol/licence number."),
    _item("15", "Recommended - housing and husbandry",
          "Housing conditions, environmental enrichment and husbandry.",
          [r"\bhous(ed|ing)\b", r"\blight[/ ]dark cycle\b", r"\benrichment\b", r"\bad libitum\b",
           r"\bcage\b"],
          "methods, housing", "Describe housing, light cycle, diet and enrichment."),
    _item("16", "Recommended - animal care and monitoring",
          "Measures to reduce pain, adverse events, humane endpoints and monitoring frequency.",
          [r"\bhumane end-?points?\b", r"\bmonitor(ed|ing) (daily|twice|for)\b", r"\banalgesi",
           r"\badverse events?\b", r"\bwelfare\b"],
          "methods, animal care", "State the humane endpoints and monitoring schedule."),
    _item("17", "Recommended - interpretation",
          "Interpretation against objectives and current theory, with limitations and bias sources.",
          [r"\blimitations?\b", r"\binterpret", r"\bbias\b"],
          "discussion", "Interpret the findings and state the limitations.", judgement=True),
    _item("18", "Recommended - generalisability",
          "How the findings might translate to other species or systems, including humans.",
          [r"\btranslat(e|ion|able)\b", r"\bgenerali[sz]ab", r"\bhuman (relevance|biology|disease)\b"],
          "discussion", "Discuss translation and generalisability.", judgement=True),
    _item("19", "Recommended - protocol registration",
          "Whether a protocol was prepared in advance and where it is registered.",
          [r"\bpre-?registered\b", r"\bpreclinicaltrials\.eu\b", r"\bprotocol (was )?registered\b",
           r"\bnot (pre-?)?registered\b"],
          "other information", "Say whether and where the protocol was registered."),
    _item("20", "Recommended - data access",
          "Where the study data can be accessed, or why access is restricted.",
          [r"\bdata (availability|are available|can be (accessed|obtained))\b",
           r"\b(Zenodo|Dryad|figshare|OSF|GEO|accession)\b"],
          "other information", "Add a data availability statement."),
    _item("21", "Recommended - declaration of interests",
          "Financial and non-financial conflicts, funding sources and the funders' role.",
          [r"\bcompeting interests?\b", r"\bconflicts? of interest\b", r"\bfund(ing|ed by|er)\b"],
          "other information", "Add interests and funding statements."),
]

_MDAR_ITEMS = [
    _item("M1", "Materials - reagents",
          "For commercial reagents (e.g. antibodies) give supplier, catalogue number and RRID.",
          [r"\bRRID\b", r"\bcat(alog(ue)?)?\.?\s*(no\.?|number|#)\b", r"\bantibod(y|ies)\b.{0,60}\b(clone|dilution)\b"],
          "methods, materials", "Add supplier, catalogue number and RRID for each reagent."),
    _item("M2", "Materials - cell materials",
          "Cell lines: species, strain, accession or supplier/catalogue/clone or RRID; primary cultures: species, sex, modification.",
          [r"\bcell lines?\b", r"\bRRID:\s?CVCL", r"\bATCC\b", r"\bauthenticat(ed|ion)\b",
           r"\bmycoplasma\b"],
          "methods, cell culture", "Identify each cell line with a repository accession or RRID."),
    _item("M3", "Materials - experimental animals",
          "Laboratory animals: species, strain, sex, age, genetic modification, and an accession, supplier or RRID.",
          [r"\b(mice|mouse|rats?|zebrafish|Drosophila|C\. ?elegans)\b",
           r"\b(male|female)\b", r"\bstrain\b", r"\bJackson Laborator|Charles River\b"],
          "methods, animals", "Identify the animals fully and give the source."),
    _item("M4", "Materials - plants and microbes",
          "Plants and microbes: species and strain, accession number where available, and source.",
          [r"\b(strain|isolate|cultivar|ecotype)\b", r"\b(E\. ?coli|S\. ?cerevisiae|Arabidopsis)\b",
           r"\baccession (number|no\.?)\b"],
          "methods, materials", "Give species, strain and accession for plant/microbial material.",
          judgement=True),
    _item("M5", "Materials - human research participants",
          "Ethics authority and reference number, confirmation of informed consent, and participant age and sex.",
          [r"\bIRB\b", r"\bethics (committee|approval)\b", r"\binformed consent\b",
           r"\bDeclaration of Helsinki\b", r"\bde-?identif"],
          "methods, ethics", "Name the IRB and reference, confirm consent, report age and sex."),
    _item("D1", "Design - study protocol",
          "For clinical trials, the trial registration number or a cited protocol DOI.",
          [r"\bNCT\d{8}\b", r"\bISRCTN\d+\b", r"\btrial registration\b"],
          "methods", "Add the trial registration identifier.", judgement=True),
    _item("D2", "Design - laboratory protocol",
          "A DOI or citation for step-by-step protocols where they are available.",
          [r"\bprotocols\.io\b", r"\bprotocol (doi|DOI)\b", r"\bStar Methods\b",
           r"\bdetailed protocol\b"],
          "methods", "Deposit the protocol and cite its DOI.", judgement=True),
    _item("D3", "Design - experimental design",
          "State whether and how sample size determination, randomisation, blinding and inclusion/exclusion were done.",
          [r"\bsample size\b", r"\brandom(ly|i[sz]ed|isation)\b", r"\bblind(ed|ing)\b",
           r"\b(inclusion|exclusion) criteria\b"],
          "methods, experimental design",
          "Say for each of the four whether it was done and how, or that it was not."),
    _item("D4", "Design - replication",
          "How many times the experiment was replicated, and whether replicates are technical or biological.",
          [r"\b(biological|technical) replicates?\b", r"\bindependent experiments?\b",
           r"\brepeated (three|3|two|2|n) times\b"],
          "methods / figure legends", "State the number and type of replicates in every legend."),
    _item("D5", "Design - ethics",
          "Ethics approval details for human, animal and field studies, with reference numbers and permits.",
          [r"\bIRB\b", r"\bIACUC\b", r"\bethics (approval|committee)\b", r"\bpermit(s)?\b",
           r"\bIBC\b|\binstitutional biosafety\b"],
          "methods, ethics", "Give the approving body and reference for each kind of work."),
    _item("D6", "Design - dual use research of concern",
          "If the study is subject to DURC, the approving authority and reference number.",
          [r"\bdual[- ]use\b", r"\bDURC\b", r"\bbiosafety (level|committee)\b", r"\bBSL-?[1-4]\b",
           r"\bexport control\b"],
          "methods, ethics", "State the DURC/biosafety review and its reference, where it applies.",
          judgement=True),
    _item("A1", "Analysis - attrition",
          "Whether any samples or data points were excluded, and whether the criteria were set in advance.",
          [r"\bexcluded\b", r"\battrition\b", r"\boutliers? (were )?removed\b",
           r"\bno data (were|was) excluded\b"],
          "methods / results", "State exclusions and whether the rule was pre-specified."),
    _item("A2", "Analysis - statistics",
          "Which statistical tests were used and why they were chosen.",
          [r"\bstatistical (test|analys[ei]s)\b", r"\b(t-?test|ANOVA|Mann-?Whitney|regression)\b",
           r"\btwo-?(sided|tailed)\b"],
          "methods, statistics", "Name each test and justify the choice."),
    _item("A3", "Analysis - data availability",
          "Whether new datasets are available, with the access route or the restriction, and an accession or DOI.",
          [r"\bdata (availability|are available)\b", r"\b(GEO|SRA|PRIDE|Zenodo|Dryad|figshare|OSF)\b",
           r"\baccession (number|code)\b", r"\bupon (reasonable )?request\b"],
          "data availability statement", "Deposit the data and give the accession, or state the restriction."),
    _item("A4", "Analysis - code availability",
          "Whether newly generated code essential to the findings is available, and where.",
          [r"\bcode (is )?available\b", r"\bgithub\.com\b", r"\bgitlab\b", r"\bsoftware availability\b",
           r"\bZenodo\b.{0,30}\bcode\b"],
          "code availability statement", "Deposit the code and link it."),
    _item("R1", "Reporting - adherence to community standards",
          "State whether relevant community guidelines were followed and whether a checklist is provided.",
          [r"\b(CONSORT|PRISMA|ARRIVE|STROBE|MIQE|MIAME|REMARK|ICMJE)\b",
           r"\breporting (guidelines?|checklist)\b"],
          "methods / cover letter", "Name the reporting guideline followed and attach its checklist."),
]

CHECKLISTS: dict[str, list[dict[str, Any]]] = {
    "consort": _CONSORT_ITEMS,
    "prisma": _PRISMA_ITEMS,
    "strobe": _STROBE_ITEMS,
    "arrive": _ARRIVE_ITEMS,
    "mdar": _MDAR_ITEMS,
}

# What the manuscript text has to look like before `checklist="auto"` picks one.
_CHECKLIST_TRIGGERS = {
    "consort": [
        r"\brandomi[sz]ed controlled trial\b", r"\brandomly (assigned|allocated)\b",
        r"\ballocation concealment\b", r"\bNCT\d{8}\b", r"\bintention[- ]to[- ]treat\b",
    ],
    "prisma": [
        r"\bsystematic review\b", r"\bmeta-?analysis\b", r"\bsearch strategy\b",
        r"\bPROSPERO\b", r"\bforest plot\b",
    ],
    "strobe": [
        r"\bcohort study\b", r"\bcase-?control study\b", r"\bcross-?sectional study\b",
        r"\bobservational study\b", r"\bperson-?years\b",
    ],
    "arrive": [
        r"\bIACUC\b", r"\bin vivo\b", r"\b(mice|mouse|rats?|zebrafish)\b",
        r"\banimal (experiments?|welfare|model)\b", r"\bhumane end-?points?\b",
    ],
}


def _line_index(text: str) -> list[str]:
    return (text or "").splitlines()


def check_manuscript_text(
    text: str, names: list[str], *, max_items: int = 0
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Evidence scan of *text* against the named checklists. Evidence != compliance."""
    lines = _line_index(text)
    rows: list[dict[str, Any]] = []
    counts = {"present": 0, "missing": 0, "unclear": 0}
    for name in names:
        for item in CHECKLISTS.get(name, []):
            hit_line = 0
            quote = ""
            for pattern in item["patterns"]:
                rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
                for i, line in enumerate(lines, start=1):
                    if rx.search(line):
                        hit_line = i
                        quote = line.strip()[:240]
                        break
                if hit_line:
                    break
            if hit_line:
                status = "present"
            elif item["judgement"]:
                status = "unclear"
            else:
                status = "missing"
            counts[status] += 1
            rows.append(
                {
                    "checklist": CHECKLIST_SOURCES[name]["name"],
                    "checklist_key": name,
                    "item_id": item["item_id"],
                    "section": item["section"],
                    "item": item["item"],
                    "status": status,
                    "evidence": {"line": hit_line, "quote": quote} if hit_line else {},
                    "where_it_should_go": item["where_it_should_go"],
                    "what_to_add": "" if status == "present" else item["what_to_add"],
                    "needs_human_judgement": bool(item["judgement"]),
                }
            )
    if max_items and max_items > 0:
        rows = rows[:max_items]
    return rows, counts


def suggest_checklists(text: str) -> list[str]:
    """Checklist keys whose trigger vocabulary actually appears in the manuscript."""
    hits: list[tuple[str, int]] = []
    for name, patterns in _CHECKLIST_TRIGGERS.items():
        score = sum(
            1 for p in patterns if re.search(p, text or "", re.IGNORECASE | re.MULTILINE)
        )
        if score:
            hits.append((name, score))
    hits.sort(key=lambda pair: (-pair[1], pair[0]))
    return [name for name, _ in hits]


# ---------------------------------------------------------------------------
# path helpers (same jail shape as analysis / game tools)
# ---------------------------------------------------------------------------


POWER_TESTS = (
    "one_sample_t",
    "paired_t",
    "two_sample_t",
    "anova_oneway",
    "one_proportion",
    "two_proportions",
    "chi_square_gof",
    "chi_square_independence",
    "correlation",
    "log_rank",
)

_TEX_ENGINES = ("latexmk", "pdflatex", "xelatex", "lualatex")


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _project_root(runtime: Any) -> Path:
    try:
        return Path(runtime.effective_project_path())
    except Exception:
        return Path.cwd()


def _resolve_read(runtime: Any, raw: str) -> Path:
    p = Path((raw or "").strip()).expanduser()
    if p.is_absolute():
        return p
    try:
        return Path(runtime.resolve_tool_path(str(p)))
    except Exception:
        return _project_root(runtime) / p


def _allowed_write_roots(runtime: Any) -> list[Path]:
    # Write roots only — allowed_roots() is view-only (Desktop/Documents/Downloads).
    roots = [_project_root(runtime), *_write_roots(runtime)]
    out: list[Path] = []
    for r in roots:
        with suppress(OSError):
            rr = r.expanduser().resolve(strict=False)
            if rr not in out:
                out.append(rr)
    return out


def _jail_error(tool: str, target: Path) -> str:
    return format_tool_error(
        f"{target} is outside the project / allowed write roots",
        code="WRITE_JAIL",
        tool_name=tool,
        suggestion=(
            "Stay inside the focus project. Pass a path under the project folder, "
            "or switch the project first."
        ),
    )


def _resolve_write(runtime: Any, raw: str, tool: str, *, default: Path) -> Path | str:
    text = (raw or "").strip()
    if not text:
        target = default
    else:
        try:
            target = Path(runtime.resolve_tool_path(text, for_write=True))
        except Exception as exc:
            return format_tool_error(
                f"write refused for {text}: {exc}",
                code="WRITE_JAIL",
                tool_name=tool,
                suggestion="Pass a path under the focus project.",
            )
    allowed = _allowed_write_roots(runtime)
    if not allowed:
        return target
    try:
        res = target.expanduser().resolve(strict=False)
    except OSError:
        return _jail_error(tool, target)
    for root in allowed:
        with suppress(ValueError, OSError):
            if res == root or res.is_relative_to(root):
                return res
    return _jail_error(tool, target)


def _parse_design(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("{"):
        with suppress(json.JSONDecodeError, TypeError):
            data = json.loads(text)
            if isinstance(data, dict):
                return data
    return {}


def _load_table_groups(
    path: Path, outcome: str, group: str
) -> tuple[dict[str, list[float]] | list[float] | None, str]:
    """Load numeric data from a delimited file. Error string on failure."""
    try:
        header, rows, _trunc = read_table(path)
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    if group:
        if not outcome:
            return None, "outcome= is required with group="
        grouped = _grouped(header, rows, outcome, group)
        if not grouped:
            return None, (
                f"no numeric values for outcome={outcome!r} grouped by {group!r}; "
                f"columns are: {', '.join(header) or '(none)'}"
            )
        return grouped, ""
    col = outcome
    if not col:
        return None, "outcome= (column name) is required with data_path="
    values = _to_floats(_column(header, rows, col))
    if not values:
        return None, (
            f"no numeric values in column {col!r}; columns are: "
            f"{', '.join(header) or '(none)'}"
        )
    return values, ""


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def register_science_tools(runtime: Any) -> None:
    """Register power, assumption, effect-size, multiplicity and manuscript tools."""

    async def power_analysis(
        test: str = "",
        solve: str = "power",
        n: float = 0.0,
        n2: float = 0.0,
        effect_size: float = 0.0,
        alpha: float = 0.05,
        target_power: float = 0.8,
        alternative: str = "two_sided",
        groups: int = 2,
        df: float = 0.0,
        props: str = "",
        ratio: float = 1.0,
    ) -> str:
        tool = "power_analysis"
        kind = (test or "").strip()
        if kind not in POWER_TESTS:
            return format_tool_error(
                f"unknown test {test!r}",
                code="UNKNOWN_TEST",
                tool_name=tool,
                suggestion=f"test= one of: {', '.join(POWER_TESTS)}",
            )
        alt = (alternative or "two_sided").strip().lower().replace("-", "_")
        if alt in ("two-sided", "twosided"):
            alt = "two_sided"
        if alt not in ("two_sided", "greater", "less"):
            alt = "two_sided"
        mode = (solve or "power").strip().lower()
        if mode in ("sample_size", "n_per_group", "nn"):
            mode = "n"
        if mode not in ("power", "n", "effect", "alpha"):
            return format_tool_error(
                f"unknown solve={solve!r}",
                code="UNKNOWN_SOLVE",
                tool_name=tool,
                suggestion='solve="power"|"n"|"effect"|"alpha"',
            )
        prop_list = _parse_floats(props)
        kw: dict[str, Any] = {
            "effect_size": float(effect_size),
            "alpha": float(alpha),
            "alternative": alt,
            "n2": float(n2),
            "groups": int(groups) if groups else 2,
            "df": float(df),
            "props": prop_list,
            "ratio": float(ratio) if ratio else 1.0,
        }
        notes = [
            "Power is computed from the design you named, not from a p-value. "
            "Post-hoc power is a function of p and carries no extra information — "
            "if the data already exist, report the effect with its interval instead.",
            "method: stdlib distributions (NormalDist + incomplete beta/gamma); "
            "see agent_science_tools for the cdf tolerances",
        ]
        try:
            if mode == "power":
                if n <= 0:
                    return format_tool_error(
                        "n is required to compute power",
                        code="MISSING_N",
                        tool_name=tool,
                        suggestion="Pass n= (per group for two_sample_t / anova_oneway; events for log_rank).",
                    )
                value = power_for(kind, n=float(n), **kw)
                solved = "power"
            elif mode == "n":
                value = solve_n(float(target_power), kind, **kw)
                solved = "n"
                if value < 0:
                    return format_tool_error(
                        "n exceeded 5e7 without reaching target_power — the effect is too small for this design",
                        code="UNREACHABLE",
                        tool_name=tool,
                        suggestion="Increase effect_size (the smallest effect worth detecting) or lower target_power.",
                    )
            elif mode == "effect":
                if n <= 0:
                    return format_tool_error(
                        "n is required to solve for effect size",
                        code="MISSING_N",
                        tool_name=tool,
                        suggestion="Pass the achieved or planned n.",
                    )
                value = solve_effect(float(target_power), kind, n=float(n), **{k: v for k, v in kw.items() if k != "effect_size"})
                solved = "effect_size"
            else:
                if n <= 0:
                    return format_tool_error(
                        "n is required to solve for alpha",
                        code="MISSING_N",
                        tool_name=tool,
                        suggestion="Pass n=.",
                    )
                value = solve_alpha(float(target_power), kind, n=float(n), **{k: v for k, v in kw.items() if k != "alpha"})
                solved = "alpha"
        except ValueError as exc:
            return format_tool_error(
                str(exc),
                code="BAD_ARGS",
                tool_name=tool,
                suggestion="Check test, n, effect_size and alpha.",
            )
        if isinstance(value, float) and math.isnan(value):
            return format_tool_error(
                f"no {solved} reaches target_power={target_power} at this design",
                code="UNREACHABLE",
                tool_name=tool,
                suggestion="Relax target_power or change the design.",
            )
        return _dump(
            {
                "test": kind,
                "solve": mode,
                "solved": solved,
                "value": value,
                "n": n if mode != "n" else value,
                "n2": n2,
                "effect_size": effect_size if mode != "effect" else value,
                "alpha": alpha if mode != "alpha" else value,
                "target_power": target_power,
                "alternative": alt,
                "groups": groups,
                "df": df,
                "props": prop_list,
                "ratio": ratio,
                "notes": notes,
            }
        )

    async def stats_assumptions(
        values: str = "",
        data_path: str = "",
        outcome: str = "",
        group: str = "",
        design: str = "",
        outcome_type: str = "",
        n_groups: int = 0,
        paired: bool = False,
        repeated: bool = False,
        covariates: int = 0,
        correlational: bool = False,
    ) -> str:
        tool = "stats_assumptions"
        data: dict[str, list[float]] | list[float] | None = None
        if (data_path or "").strip():
            path = _resolve_read(runtime, data_path)
            if not path.is_file():
                return format_tool_error(
                    f"not a file: {path}",
                    code="NOT_FOUND",
                    tool_name=tool,
                    suggestion="Pass data_path= to a csv/tsv inside the project.",
                )
            data, err = _load_table_groups(path, outcome, group)
            if err:
                return format_tool_error(
                    err, code="BAD_DATA", tool_name=tool, suggestion="Check column names with data_profile."
                )
        elif (values or "").strip():
            parsed = _parse_values(values)
            if isinstance(parsed, dict) and parsed and all(isinstance(v, list) for v in parsed.values()):
                data = {
                    str(k): [float(x) for x in v]
                    for k, v in parsed.items()
                    if isinstance(v, list)
                }
            elif isinstance(parsed, list):
                data = parsed
            else:
                return format_tool_error(
                    "values= must be a JSON array, an object of groups, or a comma list of numbers",
                    code="BAD_VALUES",
                    tool_name=tool,
                    suggestion='values="[1,2,3]" or values=\'{"a":[1,2],"b":[3,4]}\'',
                )
        checks: list[dict[str, Any]] = []
        groups_map: dict[str, list[float]] = {}
        if isinstance(data, dict):
            groups_map = data
            all_vals = [v for vals in data.values() for v in vals]
            if all_vals:
                checks.append(dagostino_pearson(all_vals))
                checks.append(outlier_scan(all_vals))
            if len(data) >= 2:
                checks.append(brown_forsythe(data))
            for name, vals in data.items():
                row = dagostino_pearson(vals)
                row = dict(row)
                row["group"] = name
                checks.append(row)
        elif isinstance(data, list) and data:
            checks.append(dagostino_pearson(data))
            checks.append(outlier_scan(data))

        spec = _parse_design(design)
        otype = (outcome_type or str(spec.get("outcome_type") or "")).strip().lower()
        ng = int(n_groups or spec.get("n_groups") or (len(groups_map) if groups_map else (2 if data else 0)))
        is_paired = bool(paired or spec.get("paired"))
        is_repeated = bool(repeated or spec.get("repeated"))
        n_cov = int(covariates or spec.get("covariates") or 0)
        is_corr = bool(correlational or spec.get("correlational"))
        recommended: list[dict[str, str]] = []
        fallbacks: list[dict[str, str]] = []
        if otype:
            if otype not in _OUTCOME_TYPES:
                return format_tool_error(
                    f"unknown outcome_type {outcome_type!r}",
                    code="BAD_DESIGN",
                    tool_name=tool,
                    suggestion=f"outcome_type= one of: {', '.join(_OUTCOME_TYPES)}",
                )
            recommended, fallbacks = _recommend(
                otype,
                max(1, ng),
                paired=is_paired,
                repeated=is_repeated,
                covariates=n_cov,
                correlational=is_corr,
            )
        if not checks and not recommended:
            return format_tool_error(
                "nothing to check: pass values= or data_path=, and/or a design (outcome_type=)",
                code="MISSING_INPUT",
                tool_name=tool,
                suggestion='stats_assumptions(values="[1,2,3]", outcome_type="continuous", n_groups=2)',
            )
        return _dump(
            {
                "checks": checks,
                "recommended": recommended,
                "fallbacks": fallbacks,
                "design": {
                    "outcome_type": otype,
                    "n_groups": ng,
                    "paired": is_paired,
                    "repeated": is_repeated,
                    "covariates": n_cov,
                    "correlational": is_corr,
                },
                "n_values": (
                    sum(len(v) for v in groups_map.values())
                    if groups_map
                    else (len(data) if isinstance(data, list) else 0)
                ),
                "notes": [
                    "A flagged assumption is not a veto. It tells you which test's "
                    "assumptions are strained and which fallback to prefer.",
                    "These checks never drop a row. Outliers are listed for inspection.",
                ],
            }
        )

    async def stats_effect_size(
        kind: str = "",
        values: str = "",
        data_path: str = "",
        outcome: str = "",
        group: str = "",
        n1: int = 0,
        n2: int = 0,
        mean1: float = 0.0,
        mean2: float = 0.0,
        sd1: float = 0.0,
        sd2: float = 0.0,
        a: int = 0,
        b: int = 0,
        c: int = 0,
        d: int = 0,
        r: float = 0.0,
        pairs: str = "",
        anova_json: str = "",
        conf_level: float = 0.95,
        hedges_correction: bool = True,
    ) -> str:
        tool = "stats_effect_size"
        k = (kind or "").strip()
        if k not in EFFECT_KINDS:
            return format_tool_error(
                f"unknown kind {kind!r}",
                code="UNKNOWN_KIND",
                tool_name=tool,
                suggestion=f"kind= one of: {', '.join(EFFECT_KINDS)}",
            )
        groups_map: dict[str, list[float]] | None = None
        pair_list: list[tuple[float, float]] | None = None
        anova: dict[str, float] | None = None
        if (data_path or "").strip():
            path = _resolve_read(runtime, data_path)
            if not path.is_file():
                return format_tool_error(
                    f"not a file: {path}",
                    code="NOT_FOUND",
                    tool_name=tool,
                    suggestion="Pass data_path= to a csv/tsv inside the project.",
                )
            data, err = _load_table_groups(path, outcome, group)
            if err:
                return format_tool_error(
                    err, code="BAD_DATA", tool_name=tool, suggestion="Check column names."
                )
            if isinstance(data, dict):
                groups_map = data
            elif isinstance(data, list) and data:
                groups_map = {"x": data}
        elif (values or "").strip():
            parsed = _parse_values(values)
            if isinstance(parsed, dict) and parsed and all(isinstance(v, list) for v in parsed.values()):
                groups_map = {
                    str(n): [float(x) for x in v]
                    for n, v in parsed.items()
                    if isinstance(v, list)
                }
            elif isinstance(parsed, dict) and {"f", "df1", "df2"} <= set(parsed):
                anova = {str(n): float(v) for n, v in parsed.items() if isinstance(v, int | float)}
        if (pairs or "").strip():
            with suppress(json.JSONDecodeError, TypeError, ValueError):
                raw = json.loads(pairs)
                if isinstance(raw, list):
                    pair_list = [
                        (float(p[0]), float(p[1]))
                        for p in raw
                        if isinstance(p, (list, tuple)) and len(p) >= 2
                    ]
        if (anova_json or "").strip():
            with suppress(json.JSONDecodeError, TypeError, ValueError):
                blob = json.loads(anova_json)
                if isinstance(blob, dict):
                    anova = {str(n): float(v) for n, v in blob.items() if isinstance(v, int | float)}
        try:
            result = effect_size_core(
                k,
                groups=groups_map,
                n1=int(n1),
                n2=int(n2),
                mean1=float(mean1),
                mean2=float(mean2),
                sd1=float(sd1),
                sd2=float(sd2),
                a=int(a),
                b=int(b),
                c=int(c),
                d=int(d),
                r=float(r),
                pairs=pair_list,
                anova=anova,
                conf_level=float(conf_level),
                hedges_correction=bool(hedges_correction),
            )
        except ValueError as exc:
            return format_tool_error(
                str(exc),
                code="BAD_ARGS",
                tool_name=tool,
                suggestion="Pass group data, summaries (n1,n2,mean1,mean2,sd1,sd2), or a 2x2 (a,b,c,d).",
            )
        result["benchmark_caveat"] = _BENCHMARK_CAVEAT
        return _dump(result)

    async def stats_multiplicity(
        pvalues: str = "",
        method: str = "holm",
        labels: str = "",
        alpha: float = 0.05,
    ) -> str:
        tool = "stats_multiplicity"
        nums = _parse_floats(pvalues)
        if not nums:
            return format_tool_error(
                "pvalues is required",
                code="MISSING_P",
                tool_name=tool,
                suggestion='pvalues="0.01,0.04,0.20" or a JSON array',
            )
        if any(p < 0 or p > 1 for p in nums):
            return format_tool_error(
                "every p-value must be in [0, 1]",
                code="BAD_P",
                tool_name=tool,
                suggestion="Pass the raw two-sided p-values, not test statistics.",
            )
        m = (method or "holm").strip().lower()
        if m == "fdr":
            m = "bh"
        if m not in MULTIPLICITY_METHODS:
            return format_tool_error(
                f"unknown method {method!r}",
                code="UNKNOWN_METHOD",
                tool_name=tool,
                suggestion=f"method= one of: {', '.join(MULTIPLICITY_METHODS)}",
            )
        try:
            adjusted = adjust_pvalues(nums, m)
        except ValueError as exc:
            return format_tool_error(str(exc), code="BAD_ARGS", tool_name=tool, suggestion="")
        names = [s for s in re.split(r"[,\n;]+", labels) if s.strip()] if labels else []
        if names and len(names) != len(nums):
            names = []
        rows = []
        for i, (p, adj) in enumerate(zip(nums, adjusted, strict=True)):
            rows.append(
                {
                    "label": names[i] if i < len(names) else f"test_{i + 1}",
                    "p": p,
                    "adjusted": adj,
                    "reject_at_alpha": adj <= float(alpha),
                }
            )
        return _dump(
            {
                "method": m,
                "alpha": float(alpha),
                "family_size": len(nums),
                "rows": rows,
                "notes": [
                    "The family is the set of p-values you passed — say what that family was "
                    "in the write-up. Holm-adjusted p <= alpha is a rejection at FWER alpha; "
                    "BH is FDR. 'none' returns the raw p-values unchanged.",
                ],
            }
        )

    async def manuscript_check(
        path: str = "",
        checklist: str = "auto",
        max_items: int = 0,
    ) -> str:
        tool = "manuscript_check"
        if not (path or "").strip():
            return format_tool_error(
                "path is required",
                code="NO_PATH",
                tool_name=tool,
                suggestion="Pass path= to a .tex/.md/.qmd/.Rmd manuscript.",
            )
        target = _resolve_read(runtime, path)
        if not target.is_file():
            return format_tool_error(
                f"not a file: {target}",
                code="NOT_FOUND",
                tool_name=tool,
                suggestion="Check the path with list_dir on its parent.",
            )
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return format_tool_error(
                f"cannot read {target}: {exc}",
                code="READ_FAIL",
                tool_name=tool,
                suggestion="The file has to be readable text, not a binary PDF.",
            )
        want = (checklist or "auto").strip().lower()
        if want in ("auto", ""):
            names = suggest_checklists(text)
            if "arrive" in names and "mdar" not in names:
                names.append("mdar")
            if not names:
                return format_tool_error(
                    "no reporting checklist matched the manuscript vocabulary",
                    code="NO_CHECKLIST",
                    tool_name=tool,
                    suggestion=(
                        "Pass checklist=consort|prisma|strobe|arrive|mdar (comma list ok). "
                        "auto only picks when the text actually looks like that design."
                    ),
                )
        else:
            names = [n.strip() for n in want.split(",") if n.strip()]
            unknown = [n for n in names if n not in CHECKLISTS]
            if unknown:
                return format_tool_error(
                    f"unknown checklist(s): {', '.join(unknown)}",
                    code="UNKNOWN_CHECKLIST",
                    tool_name=tool,
                    suggestion=f"Known: {', '.join(CHECKLISTS)}",
                )
        rows, counts = check_manuscript_text(text, names, max_items=int(max_items or 0))
        sources = {n: CHECKLIST_SOURCES[n] for n in names if n in CHECKLIST_SOURCES}
        return _dump(
            {
                "path": str(target),
                "checklists": names,
                "counts": counts,
                "rows": rows,
                "sources": sources,
                "notes": [
                    "status=present means the tool found a matching phrase — that is "
                    "evidence the item was mentioned, not that the reporting is adequate. "
                    "unclear items need a human to judge. missing items still have to be written.",
                    "Item wording here is paraphrased; canonical text is at each source URL.",
                ],
            }
        )

    async def manuscript_build(
        path: str = "",
        engine: str = "auto",
        extra_args: str = "",
        workdir: str = "",
        timeout_seconds: float = 900.0,
    ) -> str:
        tool = "manuscript_build"
        if not (path or "").strip():
            return format_tool_error(
                "path is required",
                code="NO_PATH",
                tool_name=tool,
                suggestion="Pass path= to a .tex / .qmd / .Rmd file.",
            )
        script = _resolve_read(runtime, path)
        if not script.is_file():
            return format_tool_error(
                f"not a file: {script}",
                code="NOT_FOUND",
                tool_name=tool,
                suggestion="Check the path with list_dir on its parent.",
            )
        suffix = script.suffix.lower()
        if suffix in (".sh", ".ps1", ".bat", ".cmd"):
            return format_tool_error(
                f"{suffix} scripts are shell work, not a manuscript build",
                code="USE_BASH_EXEC",
                tool_name=tool,
                suggestion="Run shell scripts with bash_exec.",
            )
        project = _project_root(runtime)
        default_wd = script.parent if script.parent.is_dir() else project
        cwd = _resolve_write(runtime, workdir, tool, default=default_wd)
        if isinstance(cwd, str):
            return cwd
        timeout = _clamp(timeout_seconds, 10.0, 1800.0, 900.0)
        want = (engine or "auto").strip().lower() or "auto"
        argv: list[str] = []
        resolved = ""
        extra: list[str] = []
        if extra_args.strip():
            extra = [a for a in re.split(r"\s+", extra_args.strip()) if a]
            banned = ("-output-directory", "--output-dir", "--output-directory")
            if any(a.startswith(b) for a in extra for b in banned):
                return format_tool_error(
                    "extra_args cannot retarget the output directory",
                    code="BAD_ARGS",
                    tool_name=tool,
                    suggestion="Compile in the project folder; do not pass -output-directory.",
                )

        def missing(name: str, install: str) -> str:
            return format_tool_error(
                f"{name} was not found for this project",
                code="NO_RUNNER",
                tool_name=tool,
                suggestion=(
                    f"Install it yourself: {install}. Remedy does not install packages "
                    "on your behalf. Run analysis_env to see what is available."
                ),
            )

        if suffix == ".tex" or want in _TEX_ENGINES:
            order = (want,) if want in _TEX_ENGINES else _TEX_ENGINES
            bin_path = ""
            for name in order:
                bin_path = _which(name, cwd)
                if bin_path:
                    resolved = name
                    break
            if not bin_path:
                return missing("a TeX engine (latexmk/pdflatex/xelatex/lualatex)", "install TeX Live or MiKTeX")
            if resolved == "latexmk":
                argv = [bin_path, "-pdf", "-interaction=nonstopmode", "-halt-on-error", *extra, str(script)]
            else:
                argv = [bin_path, "-interaction=nonstopmode", *extra, str(script)]
        elif suffix in (".qmd", ".rmd") or want in ("quarto", "rmarkdown"):
            qbin = _which("quarto", cwd)
            if qbin and want != "rmarkdown":
                argv = [qbin, "render", str(script), *extra]
                resolved = "quarto"
            elif suffix == ".rmd":
                rbin = _which("Rscript", cwd)
                if not rbin:
                    return missing("quarto or Rscript", "install Quarto, or R with rmarkdown")
                escaped = str(script).replace("\\", "/")
                argv = [rbin, "--vanilla", "-e", f'rmarkdown::render("{escaped}")']
                resolved = "rmarkdown"
            else:
                return missing("quarto", "install Quarto (quarto.org)")
        else:
            return format_tool_error(
                f"no engine for {suffix or 'this file'} (engine={want})",
                code="UNSUPPORTED_ENGINE",
                tool_name=tool,
                suggestion="Supported: .tex (latexmk/pdflatex/xelatex/lualatex), .qmd, .Rmd.",
            )

        blocked = _approval_block(runtime, tool, _argv_text(argv))
        if blocked:
            return blocked

        started = time.monotonic()
        result = await _sandbox_run(runtime, argv, cwd=cwd, timeout=timeout)
        elapsed = time.monotonic() - started
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        exit_code = int(getattr(result, "exit_code", 1) or 0)
        log_text = stdout + "\n" + stderr
        log_file = cwd / f"{script.stem}.log"
        if log_file.is_file():
            with suppress(OSError):
                log_text = log_file.read_text(encoding="utf-8", errors="replace") + "\n" + log_text
        condensed = condense_tex_log(log_text)
        pdf = cwd / f"{script.stem}.pdf"
        return _dump(
            {
                "path": str(script),
                "workdir": str(cwd),
                "engine": resolved,
                "argv": argv,
                "exit_code": exit_code,
                "ok": exit_code == 0 and not condensed["errors"],
                "elapsed_s": round(elapsed, 3),
                "timeout_s": timeout,
                "pdf": str(pdf) if pdf.is_file() else "",
                "log": condensed,
                "stdout_tail": _tail(stdout),
                "stderr_tail": _tail(stderr),
                "notes": [
                    "ok is false when the engine returned non-zero OR the log has errors. "
                    "Undefined citations are listed even on a zero exit — fix them before calling the manuscript done.",
                    "This is a compile, not an analysis run. Numbers in the paper still have to come from analysis_run.",
                ],
            }
        )

    power_analysis._remedy_timeout = 60.0  # type: ignore[attr-defined]
    stats_assumptions._remedy_timeout = 120.0  # type: ignore[attr-defined]
    stats_effect_size._remedy_timeout = 120.0  # type: ignore[attr-defined]
    stats_multiplicity._remedy_timeout = 60.0  # type: ignore[attr-defined]
    manuscript_check._remedy_timeout = 60.0  # type: ignore[attr-defined]
    manuscript_build._remedy_timeout = 1800.0  # type: ignore[attr-defined]

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "power_analysis",
        "A priori power / sample size for a named test (stdlib distributions, no scipy). "
        "solve=power (default) | n | effect | alpha. n is per group for two_sample_t / "
        "anova_oneway and events for log_rank. Post-hoc power is refused in spirit: "
        "if the data exist, report the effect interval instead.",
        power_analysis,
        {
            "type": "object",
            "properties": {
                "test": {
                    "type": "string",
                    "description": "one_sample_t|paired_t|two_sample_t|anova_oneway|"
                    "one_proportion|two_proportions|chi_square_gof|"
                    "chi_square_independence|correlation|log_rank",
                },
                "solve": {"type": "string", "description": "power|n|effect|alpha", "default": "power"},
                "n": {"type": "number"},
                "n2": {"type": "number"},
                "effect_size": {"type": "number"},
                "alpha": {"type": "number", "default": 0.05},
                "target_power": {"type": "number", "default": 0.8},
                "alternative": {"type": "string", "default": "two_sided"},
                "groups": {"type": "integer", "default": 2},
                "df": {"type": "number"},
                "props": {"type": "string", "description": "Comma/JSON proportions for proportion tests"},
                "ratio": {"type": "number", "default": 1},
            },
            "required": ["test"],
        },
    )
    reg.register_builtin_handler(
        "stats_assumptions",
        "Assumption checks (D'Agostino-Pearson normality, refused below n=20; "
        "Brown-Forsythe; IQR/MAD outliers) plus recommended tests for a described "
        "design. Never guesses the design; never drops a row. Pass values= or "
        "data_path= + outcome= + group=, and/or outcome_type= / design JSON.",
        stats_assumptions,
        {
            "type": "object",
            "properties": {
                "values": {"type": "string"},
                "data_path": {"type": "string"},
                "outcome": {"type": "string"},
                "group": {"type": "string"},
                "design": {"type": "string", "description": "JSON object of design fields"},
                "outcome_type": {
                    "type": "string",
                    "description": "continuous|binary|count|ordinal|nominal|time_to_event|proportion",
                },
                "n_groups": {"type": "integer"},
                "paired": {"type": "boolean"},
                "repeated": {"type": "boolean"},
                "covariates": {"type": "integer"},
                "correlational": {"type": "boolean"},
            },
        },
    )
    reg.register_builtin_handler(
        "stats_effect_size",
        "One effect size with an interval and the method that produced it "
        "(Cohen's d / Hedges' g / Glass's delta / Cliff's delta / r / OR / RR / RD / "
        "NNT / Cramer's V / eta^2 / omega^2). Sidecar has no scipy — numerics are stdlib.",
        stats_effect_size,
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "values": {"type": "string"},
                "data_path": {"type": "string"},
                "outcome": {"type": "string"},
                "group": {"type": "string"},
                "n1": {"type": "integer"},
                "n2": {"type": "integer"},
                "mean1": {"type": "number"},
                "mean2": {"type": "number"},
                "sd1": {"type": "number"},
                "sd2": {"type": "number"},
                "a": {"type": "integer"},
                "b": {"type": "integer"},
                "c": {"type": "integer"},
                "d": {"type": "integer"},
                "r": {"type": "number"},
                "pairs": {"type": "string"},
                "anova_json": {"type": "string"},
                "conf_level": {"type": "number", "default": 0.95},
                "hedges_correction": {"type": "boolean", "default": True},
            },
            "required": ["kind"],
        },
    )
    reg.register_builtin_handler(
        "stats_multiplicity",
        "Adjust a family of p-values: bonferroni, holm, hochberg, bh (FDR), by, none. "
        "Exact step-up/step-down algorithms, stdlib only. Say what the family was.",
        stats_multiplicity,
        {
            "type": "object",
            "properties": {
                "pvalues": {"type": "string"},
                "method": {"type": "string", "default": "holm"},
                "labels": {"type": "string"},
                "alpha": {"type": "number", "default": 0.05},
            },
            "required": ["pvalues"],
        },
    )
    reg.register_builtin_handler(
        "manuscript_check",
        "Evidence scan of a manuscript against CONSORT / PRISMA / STROBE / ARRIVE / MDAR. "
        "present = a matching phrase was found, not that reporting is adequate. "
        "checklist=auto only picks when the text looks like that design. Item wording is "
        "paraphrased; canonical text is at the source URL in the payload.",
        manuscript_check,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "checklist": {"type": "string", "default": "auto"},
                "max_items": {"type": "integer"},
            },
            "required": ["path"],
        },
    )
    reg.register_builtin_handler(
        "manuscript_build",
        "Compile a manuscript: .tex via latexmk/pdflatex/xelatex/lualatex, .qmd via quarto, "
        ".Rmd via quarto or rmarkdown::render. Returns condensed TeX/biber errors, undefined "
        "citations/references, and the pdf path when one was written. Same sandbox + approval "
        "gate as bash_exec. A compile is not an analysis run.",
        manuscript_build,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "engine": {"type": "string", "default": "auto"},
                "extra_args": {"type": "string"},
                "workdir": {"type": "string"},
                "timeout_seconds": {"type": "number", "default": 900},
            },
            "required": ["path"],
        },
    )



