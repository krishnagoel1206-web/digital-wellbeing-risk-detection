"""Proof that the embedded interpreter AND the ML stack work on the device.

A successful Gradle build proves only that wheels were downloaded and packed
into the APK. It says nothing about whether they load: a native extension
compiled against a different numpy C API, or against a newer Android API than
the phone provides, fails at IMPORT time on the device and nowhere earlier.

So each import is attempted separately and reported individually, rather than
in one block where the first failure would hide the rest.
"""

import platform
import sys


def report() -> str:
    lines = [
        f"python {platform.python_version()} on {platform.machine()} "
        f"(64-bit={sys.maxsize > 2**32})"
    ]

    for name in ("numpy", "scipy", "pandas", "sklearn", "joblib"):
        try:
            mod = __import__(name)
            lines.append(f"  {name} {getattr(mod, '__version__', '?')} OK")
        except Exception as exc:
            lines.append(f"  {name} FAILED: {type(exc).__name__}: {exc}")

    return "\n".join(lines)


def arithmetic_check() -> str:
    """Actually USE the stack, not merely import it.

    Importing numpy proves the shared library loads; it does not prove the
    BLAS backing it works, and a broken BLAS surfaces as a wrong answer or a
    crash on the first real matrix operation rather than on import.
    """
    try:
        import numpy as np
        from sklearn.decomposition import PCA

        rng = np.random.default_rng(0)
        x = rng.normal(size=(50, 4))
        fitted = PCA(n_components=2).fit(x)
        ratio = fitted.explained_variance_ratio_
        return f"PCA ran: explained variance {ratio[0]:.3f}, {ratio[1]:.3f}"
    except Exception as exc:
        return f"arithmetic FAILED: {type(exc).__name__}: {exc}"
