from app.pipeline.bundle import BundleAnalysis


def sm_in_bundle(smart_wallets: frozenset[str], bundle: BundleAnalysis) -> frozenset[str]:
    """Smart wallets that are also members of a bundle cluster.

    Bundle clusters are built from the earliest buyers only, so a smart wallet that bought later
    is never reported here.
    """
    return smart_wallets & bundle.bundled_wallets
