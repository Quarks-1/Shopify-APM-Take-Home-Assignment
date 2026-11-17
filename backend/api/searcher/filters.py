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


def evaluate(fields, snapshot, tolerance):
    """Returns (passes, reasons, size_status, weight_status)."""
    reasons = []
    tol_pct = round((tolerance - 1) * 100)
    tol_txt = f'+{tol_pct}%'

    # ---- price / currency
    currency = (fields.get('currency') or '').upper()
    if currency != 'USD':
        reasons.append(f'currency is not USD: {currency or "unknown"}')
    limit = snapshot.get('priceLimit')
    cents = fields.get('price_cents')
    if limit is not None and cents is not None and cents > round(limit * 100):
        reasons.append(f'over price limit: {_money(cents)} > ${limit:,.2f}')

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
        face = sorted(v for v in (px, py, pz) if v is not None)[-2:]
        if len(face) == 2:
            of = sorted([ox, oh])
            if any(p > o * tolerance for p, o in zip(face, of)):
                violations.append(
                    f'too large: {_n(face[0])} x {_n(face[1])} in face exceeds '
                    f'{_n(of[0])} x {_n(of[1])} in ({tol_txt})'
                )
        elif len(face) == 1 and face[0] > max(ox, oh) * tolerance:
            violations.append(
                f'too large: {_n(face[0])} in face side exceeds {_n(max(ox, oh))} in ({tol_txt})'
            )
        if longest is not None and longest > max(ox, oh) * tolerance:
            violations.append(
                f'too large: ~{_n(longest)} in longest side exceeds '
                f'{_n(max(ox, oh))} in ({tol_txt})'
            )
    elif ox is not None and oy is not None:
        if px is not None and py is not None:
            ps, os_ = sorted([px, py]), sorted([ox, oy])
            if any(p > o * tolerance for p, o in zip(ps, os_)):
                violations.append(
                    f'too large: {_n(px)} x {_n(py)} in footprint exceeds '
                    f'{_n(ox)} x {_n(oy)} in ({tol_txt})'
                )
        else:
            one = px if px is not None else py
            if one is not None and one > max(ox, oy) * tolerance:
                violations.append(
                    f'too large: {_n(one)} in footprint side exceeds '
                    f'{_n(max(ox, oy))} in ({tol_txt})'
                )
        if longest is not None:
            biggest = max(v for v in (ox, oy, oz) if v is not None)
            if longest > biggest * tolerance:
                violations.append(
                    f'too large: ~{_n(longest)} in longest side exceeds '
                    f'{_n(biggest)} in ({tol_txt})'
                )
    if not wall and oz is not None and pz is not None and pz > oz * tolerance:
        violations.append(
            f'too tall: {_n(pz)} in height exceeds {_n(oz)} in ({tol_txt})'
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
