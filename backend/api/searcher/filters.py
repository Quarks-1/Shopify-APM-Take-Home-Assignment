"""Hard-constraint evaluation (price, size, weight).

Style, material and product type are soft constraints and are deliberately
NOT filtered here; the agent judges them.
"""


def _n(v):
    """Compact number: 46 -> '46', 46.5 -> '46.5', 46.25 -> '46.25'."""
    return f'{round(float(v), 2):g}'


def _money(cents):
    return f'${cents / 100:,.2f}'


def _usd(value):
    value = float(value)
    return f'${value:,.0f}' if value == int(value) else f'${value:,.2f}'


def is_wall_mode(snapshot):
    """Wall art (or anything on a wall space): dims are width x height; y is depth."""
    return snapshot.get('type') == 'wall-art' or (snapshot.get('space') or {}).get('type') == 'wall'


def _fuzz():
    from django.conf import settings
    return float(getattr(settings, 'SEARCHER_PRICE_FUZZ', 0.15))


def price_bounds(snapshot, fuzz):
    """Effective (min_cents, max_cents), widened by `fuzz` on fuzzy ends."""
    lo = snapshot.get('priceMin')
    hi = snapshot.get('priceLimit')
    if lo is not None:
        lo = round(lo * (1 - fuzz) * 100) if snapshot.get('priceMinFuzzy') else round(lo * 100)
    if hi is not None:
        hi = round(hi * (1 + fuzz) * 100) if snapshot.get('priceLimitFuzzy') else round(hi * 100)
    return lo, hi


def price_status(cents, snapshot):
    """'in-range' | 'slightly-over' | 'slightly-under' vs the stated bounds."""
    if cents is None or cents <= 0:
        return 'in-range'
    hi = snapshot.get('priceLimit')
    lo = snapshot.get('priceMin')
    if hi is not None and snapshot.get('priceLimitFuzzy') and cents > round(hi * 100):
        return 'slightly-over'
    if lo is not None and snapshot.get('priceMinFuzzy') and cents < round(lo * 100):
        return 'slightly-under'
    return 'in-range'


def evaluate(fields, snapshot, tolerance):
    """Returns (passes, reasons, size_status, weight_status)."""
    reasons = []
    tol_pct = round((tolerance - 1) * 100)
    tol_txt = f'+{tol_pct}%'

    # ---- price / currency
    currency = (fields.get('currency') or '').upper()
    if currency != 'USD':
        reasons.append(f'currency is not USD: {currency or "unknown"}')
    fuzz = _fuzz()
    pct = round(fuzz * 100)
    lo_cents, hi_cents = price_bounds(snapshot, fuzz)
    limit = snapshot.get('priceLimit')
    cents = fields.get('price_cents')
    if hi_cents is not None and cents is not None and cents > hi_cents:
        if snapshot.get('priceLimitFuzzy'):
            reasons.append(
                f'over price limit: {_money(cents)} > around ${limit:,.2f} (+{pct}%)')
        else:
            reasons.append(f'over price limit: {_money(cents)} > ${limit:,.2f}')
    minimum = snapshot.get('priceMin')
    if lo_cents is not None and cents is not None and 0 < cents < lo_cents:
        if snapshot.get('priceMinFuzzy'):
            reasons.append(
                f'under price minimum: {_money(cents)} < around ${minimum:,.2f} (-{pct}%)')
        else:
            reasons.append(f'under price minimum: {_money(cents)} < ${minimum:,.2f}')

    # ---- rating (unrated products are excluded when a minimum is set)
    min_rating = snapshot.get('minRating')
    if min_rating is not None:
        rv = fields.get('rating_value')
        if rv is None:
            reasons.append('rating unknown')
        elif rv < min_rating:
            reasons.append(f'below rating minimum: {_n(rv)} < {_n(min_rating)}')

    # ---- size
    if cents is not None and cents <= 0:
        reasons.append('price unknown')
    wall = is_wall_mode(snapshot)
    odims = snapshot.get('dims') or {}
    ox, oy, oz = odims.get('x'), odims.get('y'), odims.get('z')
    # Mirror the frontend's footprint(): depth defaults to width on the
    # floor, and a wall object's height is z ?? y ?? x.
    oh = next((v for v in (oz, oy, ox) if v is not None), None)
    if not wall and oy is None:
        oy = ox
    pdims = fields.get('dims') or {}
    px, py, pz = pdims.get('x'), pdims.get('y'), pdims.get('z')
    longest = fields.get('dims_longest')
    violations = []

    if wall and ox is not None:
        # Orientation matters: a rotated product does not qualify. The face is
        # width x height in axis order; with three values the thinnest is depth.
        vals = [v for v in (px, py, pz) if v is not None]
        if len(vals) == 3:
            vals.remove(min(vals))
        face = vals
        if len(face) == 2:
            if face[0] > ox or face[1] > oh:
                violations.append(
                    f'too large: {_n(face[0])} x {_n(face[1])} in face exceeds '
                    f'{_n(ox)} x {_n(oh)} in (rotated sizes do not count)'
                )
        elif len(face) == 1 and face[0] > max(ox, oh):
            violations.append(
                f'too large: {_n(face[0])} in face side exceeds {_n(max(ox, oh))} in'
            )
        if longest is not None and longest > max(ox, oh):
            violations.append(
                f'too large: ~{_n(longest)} in longest side exceeds '
                f'{_n(max(ox, oh))} in'
            )
    elif ox is not None and oy is not None:
        if px is not None and py is not None:
            ps, os_ = sorted([px, py]), sorted([ox, oy])
            if any(p > o for p, o in zip(ps, os_)):
                violations.append(
                    f'too large: {_n(px)} x {_n(py)} in footprint exceeds '
                    f'{_n(ox)} x {_n(oy)} in'
                )
        else:
            one = px if px is not None else py
            if one is not None and one > max(ox, oy):
                violations.append(
                    f'too large: {_n(one)} in footprint side exceeds '
                    f'{_n(max(ox, oy))} in'
                )
        if longest is not None:
            biggest = max(v for v in (ox, oy, oz) if v is not None)
            if longest > biggest:
                violations.append(
                    f'too large: ~{_n(longest)} in longest side exceeds '
                    f'{_n(biggest)} in'
                )
    if not wall and oz is not None and pz is not None and pz > oz:
        violations.append(
            f'too tall: {_n(pz)} in height exceeds {_n(oz)} in'
        )

    if violations:
        size_status = 'too_big'
        reasons.extend(violations)
    elif wall and len([v for v in (px, py, pz) if v is not None]) >= 2:
        size_status = 'fits'
    elif not wall and px is not None and py is not None and (oz is None or pz is not None):
        size_status = 'fits'
    else:
        size_status = 'unverified'

    # ---- weight
    ow, pw = snapshot.get('weight'), fields.get('weight_lb')
    if ow is not None and pw is not None:
        if pw > ow * tolerance:
            weight_status = 'too_heavy'
            reasons.append(f'too heavy: {_n(pw)} lb exceeds {_n(ow)} lb ({tol_txt})')
        else:
            weight_status = 'fits'
    else:
        weight_status = 'unverified'

    if size_status == 'unverified':
        reasons.append('size unverified')

    return (not reasons), reasons, size_status, weight_status
