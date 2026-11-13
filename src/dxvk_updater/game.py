"""Conservative build identification; unknown builds require player approval."""
import re


def game_support(folder):
    try:
        with (folder / "mtndew.dll").open("rb") as stream:
            data = stream.read(32 * 1024**2 + 1)
        if len(data) > 32 * 1024**2:
            return "Unrecognized ElDewrito build", False
        development = re.search(rb"\b0\.7\.\d+-[0-9A-Za-z._-]+", data)
        if development:
            return "ElDewrito " + development.group().decode("ascii"), False
        versions = set(re.findall(rb"\x00(0\.7\.[01])\x00", data))
        if versions:
            return "ElDewrito " + max(versions).decode("ascii") + " (stable)", True
    except OSError:
        pass
    return "Unrecognized ElDewrito build", False
