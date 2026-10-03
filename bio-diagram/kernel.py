import os
import sys
import shutil
import importlib.util


def biodiagram_path():
    """Absolute path of scripts/biodiagram.py inside this skill."""
    here = os.path.dirname(sys._getframe().f_code.co_filename)
    if not here:
        raise RuntimeError("bio-diagram skill directory unavailable in this runtime")
    return os.path.join(here, "scripts", "biodiagram.py")


def load_biodiagram():
    """Import the biodiagram library and return the module: bd = load_biodiagram()."""
    path = biodiagram_path()
    spec = importlib.util.spec_from_file_location("biodiagram", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["biodiagram"] = mod
    spec.loader.exec_module(mod)
    return mod


def vendor_biodiagram(dest_dir="src"):
    """Copy biodiagram.py (and the example figure script) into the project so figure
    scripts run from committed code without the skill installed. Returns the copied paths."""
    os.makedirs(dest_dir, exist_ok=True)
    src = biodiagram_path()
    out = [shutil.copy(src, os.path.join(dest_dir, "biodiagram.py"))]
    ex = os.path.join(os.path.dirname(os.path.dirname(src)), "examples", "make_perturbseq_schematic.py")
    if os.path.exists(ex):
        out.append(shutil.copy(ex, os.path.join(dest_dir, "example_make_perturbseq_schematic.py")))
    return out
