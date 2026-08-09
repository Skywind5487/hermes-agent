from __future__ import annotations

from scenarios import Builder, Scenario


def make_focused_scenarios(C: int = 300) -> list[Scenario]:
    """Small production-shaped decision surface for #54.

    Keep three workload classes distinct:

    1. current Hermes-normal: shallow ancestry and K reached immediately;
    2. historical compatibility: the observed depth-14 / size-15 envelope,
       ranked to maximize repeated ancestry before K=3 can be satisfied;
    3. malformed/pathological safety lives in ``make_pathological_scenarios``
       and is intentionally not treated as normal performance evidence.
    """
    out: list[Scenario] = []

    b = Builder()
    candidates = [b.root() for _ in range(C)]
    out.append(
        Scenario(
            "focused_normal_roots_c300_k3",
            b.sessions,
            candidates,
            3,
            "Hermes-normal depth0: distinct roots; candidate-level early stop after rank 3",
        )
    )

    b = Builder()
    candidates = [b.chain(1)[-1] for _ in range(C)]
    out.append(
        Scenario(
            "focused_normal_depth1_c300_k3",
            b.sessions,
            candidates,
            3,
            "Hermes-normal observed positive ancestry ceiling: independent depth1; K at rank 3",
        )
    )

    b = Builder()
    chain = b.chain(14)  # 15 physical sessions, matching the frozen historical extreme.
    root2 = b.root()
    root3 = b.root()
    candidates = list(reversed(chain)) + [root2, root3]
    out.append(
        Scenario(
            "focused_historical_depth14_size15_worst_k3",
            b.sessions,
            candidates,
            3,
            "Historical compatibility: all 15 members deepest-to-root, then two roots; maximizes repeated ancestry before K=3",
        )
    )

    b = Builder()
    chain = b.chain(14)
    candidates = list(reversed(chain))
    out.append(
        Scenario(
            "focused_historical_depth14_size15_fullconsume_k3",
            b.sessions,
            candidates,
            3,
            "Historical compatibility: one 15-node lineage only; K unreachable so all distinct members are consumed",
        )
    )

    return out
