# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Local Preset Overlay"
# description = "Rule-based local overrides for system presets, plus IP printers. Generates inherited presets in a managed local bundle."
# author = "dodomarg"
# version = "0.1.0"
# type = "script"
# ///
"""Local Preset Overlay.

Rules say: when the printer (and/or base preset) matches these conditions, override
these keys on these base presets. Results are written as presets that inherit the
system preset, inside a managed local bundle, which is then reloaded. System
profiles are never modified.
"""
import fnmatch
import hashlib
import json
import os
import re
import time
from pathlib import Path

BUNDLE_ID = "local-preset-overlay"
MARKER = "[LP]"
MAX_OUTPUT = 200
KIND_DIR = {"process": "process", "filament": "filament", "printer": "machine"}
VARIANT_KEYS = {
    "process": ["print_extruder_id", "print_extruder_variant"],
    "printer": ["printer_extruder_id", "printer_extruder_variant"],
    "filament": ["filament_extruder_variant"],
}


# ---------------------------------------------------------------- engine
def bare_name(name):
    m = re.match(r"^_local/[^/]+/(.*)$", name or "")
    return m.group(1) if m else (name or "")


def safe_filename(name, limit=120):
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    if len(clean) > limit:
        h = hashlib.sha1(name.encode()).hexdigest()[:8]
        clean = clean[: limit - 9].rstrip(" .") + "-" + h
    return clean or "preset"


def split_vec(v):
    return [x.strip() for x in str(v).split(";")] if v is not None else []


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _cmp(op, actual, expected):
    a, e = str(actual).strip(), str(expected).strip()
    if op in ("eq", "ne"):
        an, en = _num(a), _num(e)
        same = (an == en) if an is not None and en is not None else a.lower() == e.lower()
        return same if op == "eq" else not same
    if op == "contains":
        return e.lower() in a.lower()
    if op == "glob":
        return fnmatch.fnmatchcase(a.lower(), e.lower())
    if op == "in":
        opts = [str(o) for o in expected] if isinstance(expected, list) else [s.strip() for s in e.split(",")]
        return any(_cmp("eq", a, o) for o in opts)
    an, en = _num(a), _num(e)
    if an is None or en is None:
        return False
    return {"gt": an > en, "lt": an < en, "ge": an >= en, "le": an <= en}.get(op, False)


def cond_match(cond, printer, base):
    """Conditions read the printer config; keys prefixed `base.` read the base preset."""
    key, op, expected = cond["key"], cond.get("op", "eq"), cond.get("value", "")
    if key == "printer.name":
        vals = [bare_name(printer["name"])]
    elif key == "base.name":
        vals = [bare_name(base["name"])]
    elif key.startswith("base."):
        vals = split_vec(base["values"].get(key[5:]))
    else:
        vals = split_vec(printer["values"].get(key))
    if not vals:
        return False
    hits = [_cmp(op, v, expected) for v in vals]
    return all(hits) if op == "ne" else any(hits)


def rule_matches(rule, printer, base):
    if not rule.get("enabled", True):
        return False
    if not any(fnmatch.fnmatchcase(bare_name(base["name"]), g) for g in rule.get("apply_to", [])):
        return False
    return all(cond_match(c, printer, base) for c in rule.get("match", []))


def build_override(key, value, base_value, kind):
    """A list value is used verbatim. A scalar is expanded to the base's vector length;
    single-element filament vectors need an explicit one-item list in the rule."""
    if isinstance(value, list):
        return [str(x) for x in value]
    value = str(value)
    if base_value is not None and ";" in str(base_value):
        if ";" in value:  # already a full vector, e.g. from the parameter picker
            return split_vec(value)
        return [value] * len(split_vec(base_value))
    return value


def generate_name(base, printer):
    return "%s %s @%s" % (bare_name(base["name"]), MARKER, bare_name(printer["name"]))


def plan(config, presets):
    """presets: {"printer"|"process"|"filament": [{"name","is_system","values"}]}.
    Returns {"items": [...], "conflicts": [...], "warnings": [...]}"""
    items, conflicts, warnings = [], [], []
    printers = [p for p in presets.get("printer", []) if not p["name"].startswith("_local/%s/" % BUNDLE_ID)]
    for kind in ("process", "filament"):
        rules = [r for r in config.get("rules", []) if r.get("target") == kind and r.get("enabled", True)]
        if not rules:
            continue
        for base in presets.get(kind, []):
            if not base.get("is_system") or MARKER in base["name"]:
                continue
            for printer in printers:
                hits = [r for r in rules if rule_matches(r, printer, base)]
                if not hits:
                    continue
                merged, owner, clash = {}, {}, None
                for r in sorted(hits, key=lambda r: r.get("priority", 0)):
                    label = r.get("name", r.get("id"))
                    for k, v in r.get("set", {}).items():
                        if k in owner and owner[k][0] == r.get("priority", 0) and merged[k] != v:
                            clash = (k, owner[k][1], label)
                        merged[k], owner[k] = v, (r.get("priority", 0), label)
                name = generate_name(base, printer)
                if clash:
                    conflicts.append({"preset": name, "key": clash[0], "rules": [clash[1], clash[2]]})
                    continue
                data = {"version": "", "name": name, "from": "User", "inherits": base["name"],
                        "compatible_printers": [printer["name"]]}
                for vk in VARIANT_KEYS[kind]:
                    if vk in base["values"]:
                        data[vk] = split_vec(base["values"][vk])
                for k, v in merged.items():
                    data[k] = build_override(k, v, base["values"].get(k), kind)
                items.append({"kind": kind, "name": name, "data": data,
                              "rules": sorted({o[1] for o in owner.values()})})
    by_name = {p["name"]: p for p in presets.get("printer", [])}
    for ip in config.get("ip_printers", []):
        base = by_name.get(ip.get("base", ""))
        if not base or not base.get("is_system"):
            warnings.append("IP printer '%s': base must be an existing system printer" % ip.get("name"))
            continue
        if not ip.get("name") or not ip.get("host"):
            warnings.append("IP printer needs name and host")
            continue
        data = {"version": "", "name": ip["name"], "from": "User", "inherits": base["name"],
                "print_host": ip["host"], "host_type": ip.get("host_type", "octoprint")}
        for src, dst in (("api_key", "printhost_apikey"), ("port", "printhost_port"),
                         ("user", "printhost_user"), ("password", "printhost_password")):
            if ip.get(src):
                data[dst] = str(ip[src])
        for vk in VARIANT_KEYS["printer"]:
            if vk in base["values"]:
                data[vk] = split_vec(base["values"][vk])
        items.append({"kind": "printer", "name": ip["name"], "data": data, "rules": ["ip printer"]})
    if len(items) > MAX_OUTPUT:
        warnings.append("%d presets exceed the cap of %d; narrow your rules" % (len(items), MAX_OUTPUT))
        items = []
    return {"items": items, "conflicts": conflicts, "warnings": warnings}


def write_bundle(bundle_dir, items):
    """Rewrite the managed bundle: write all items, delete stale files. Returns file count."""
    bundle_dir = Path(bundle_dir)
    for sub in KIND_DIR.values():
        (bundle_dir / sub).mkdir(parents=True, exist_ok=True)
    keep = set()
    for it in items:
        path = bundle_dir / KIND_DIR[it["kind"]] / (safe_filename(it["name"]) + ".json")
        keep.add(path)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(it["data"], indent=4, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    for sub in KIND_DIR.values():
        for f in (bundle_dir / sub).glob("*.json"):
            if f not in keep:
                f.unlink()
    meta_path = bundle_dir / "bundle_metadata.json"
    now = int(time.time())
    imported = now
    if meta_path.exists():
        try:
            imported = json.loads(meta_path.read_text(encoding="utf-8")).get("imported_time", now)
        except ValueError:
            pass
    meta = {"id": bundle_dir.name, "name": "Local Preset Overlay", "version": "1.0.0",
            "description": "Generated by the Local Preset Overlay plugin. Edits are overwritten.",
            "author": "Local Preset Overlay", "imported_time": imported, "updated_time": now}
    meta_path.write_text(json.dumps(meta, indent=4), encoding="utf-8")
    return len(keep)


# ---------------------------------------------------------------- storage
def data_dir():
    return Path(__file__).resolve().parents[2]


def store_path():
    return Path(__file__).resolve().parent / "overlay_rules.json"


def load_config(path=None):
    p = Path(path) if path else store_path()
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("rules", [])
    cfg.setdefault("ip_printers", [])
    return cfg


def save_config(cfg, path=None):
    p = Path(path) if path else store_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------- Orca glue
def user_folder(bundle):
    for coll in (bundle.printers, bundle.prints, bundle.filaments):
        for n in coll.preset_names():
            f = coll.find_preset(n).file
            m = re.search(r"[/\\]user[/\\]([^/\\]+)[/\\]", f or "")
            if m:
                return m.group(1)
    return "default"


def snapshot(bundle):
    out = {}
    for kind, coll in (("printer", bundle.printers), ("process", bundle.prints), ("filament", bundle.filaments)):
        lst = []
        for n in coll.preset_names():
            p = coll.find_preset(n)
            if p.is_default:
                continue
            lst.append({"name": p.name, "is_system": bool(p.is_system),
                        "values": {k: p.config_value(k) for k in p.config_keys()}})
        out[kind] = lst
    return out


def run_plan(orca):
    return plan(load_config(), snapshot(orca.host.preset_bundle()))


def run_apply(orca):
    bundle = orca.host.preset_bundle()
    result = plan(load_config(), snapshot(bundle))
    bdir = data_dir() / "user" / user_folder(bundle) / "_local" / BUNDLE_ID
    n = write_bundle(bdir, result["items"])
    try:
        orca.host.preset.bundles.reload_local(BUNDLE_ID)
    except Exception as e:  # surfaced to the UI
        return {"ok": False, "error": "Reload failed: %s" % e, "written": n, **result}
    return {"ok": True, "written": n, **result}


HTML = r"""<!doctype html><meta charset="utf-8"><body style="font:13px sans-serif;margin:10px">
<h3>Local Preset Overlay</h3>
<p>Rules (JSON list). Each: <code>{"id","name","enabled","priority","target":"process|filament","apply_to":["glob of base preset names"],
"match":[{"key":"nozzle_diameter","op":"eq","value":"0.4"}],"set":{"outer_wall_line_width":"0.45"}}</code><br>
Ops: eq ne contains in glob gt lt ge le. Keys: printer config key, <code>printer.name</code>, <code>base.name</code>, <code>base.&lt;key&gt;</code>.</p>
<textarea id="rules" style="width:100%;height:240px;font-family:monospace"></textarea>
<h4>IP printers (JSON list)</h4>
<p><code>{"name","host","host_type":"octoprint","base":"existing system printer name","api_key":""}</code></p>
<textarea id="ips" style="width:100%;height:90px;font-family:monospace"></textarea>
<p>Pick parameters from the native editor list:
<select id="kind"><option>process</option><option>filament</option><option>printer</option></select>
<button onclick="window.orca.postMessage({cmd:'pick',kind:kind.value})">Pick parameters...</button>
(adds a draft rule; set its <code>match</code> and <code>apply_to</code>)</p>
<p><button onclick="send('save')">Save</button> <button onclick="send('preview')">Preview</button>
<button onclick="send('apply')">Apply</button></p><pre id="out"></pre>
<script>
function send(cmd){try{window.orca.postMessage({cmd:cmd,rules:JSON.parse(rules.value||'[]'),ips:JSON.parse(ips.value||'[]')});}
catch(e){out.textContent='Invalid JSON: '+e}}
window.orca.onMessage(function(m){
 if(m.picked){var l=JSON.parse(rules.value||'[]');l.push({id:'rule-'+(l.length+1),name:'New rule',enabled:true,priority:0,
  target:m.kind==='filament'?'filament':'process',apply_to:['*'],match:[{key:'nozzle_diameter',op:'eq',value:'0.4'}],set:m.picked});
  rules.value=JSON.stringify(l,null,2);}
 if(m.error){out.textContent='ERROR: '+m.error;}
 if(m.config){rules.value=JSON.stringify(m.config.rules,null,2);ips.value=JSON.stringify(m.config.ip_printers,null,2);}
 if(m.result){var r=m.result;out.textContent=(r.ok===false?'ERROR: '+r.error+'\n':'')+(r.written!==undefined?'Written: '+r.written+'\n':'')
  +'Presets:\n'+(r.items||[]).map(function(i){return ' - ['+i.kind+'] '+i.name+'  <= '+i.rules.join(', ')}).join('\n')
  +'\nConflicts:\n'+(r.conflicts||[]).map(function(c){return ' ! '+c.preset+' key '+c.key+' ('+c.rules.join(' vs ')+')'}).join('\n')
  +'\nWarnings:\n'+(r.warnings||[]).join('\n');}
});
window.orca.postMessage({cmd:'load'});
</script></body>"""


try:
    import orca
except ImportError:  # imported outside Orca (tests)
    orca = None

if orca is not None:

    class OverlayScript(orca.script.ScriptPluginCapabilityBase):
        def get_name(self):
            return "Local Preset Overlay"

        def get_config(self):
            return json.dumps(load_config())

        def save_config(self, json_str):
            save_config(json.loads(json_str))

        def execute(self):
            holder = {}

            def on_message(data):
                if isinstance(data, str):
                    data = json.loads(data)
                win, cmd = holder["win"], data.get("cmd")
                try:
                    if cmd == "load":
                        win.post({"config": load_config()})
                        return
                    if cmd == "pick":
                        pick = getattr(orca.host.preset, "pick_parameters", None)
                        if pick is None:
                            win.post({"error": "This Orca build has no parameter picker (needs the fork patch)."})
                            return
                        picked = pick(data.get("kind", "process"), "", "Choose parameters to override")
                        if picked:
                            win.post({"picked": picked, "kind": data.get("kind", "process")})
                        return
                    save_config({"rules": data.get("rules", []), "ip_printers": data.get("ips", [])})
                    if cmd == "preview":
                        win.post({"result": {"ok": True, **run_plan(orca)}})
                    elif cmd == "apply":
                        win.post({"result": run_apply(orca)})
                    else:
                        win.post({"result": {"ok": True, "items": [], "conflicts": [], "warnings": ["Saved"]}})
                except Exception as e:
                    win.post({"result": {"ok": False, "error": str(e), "items": []}})

            holder["win"] = orca.host.ui.create_window(HTML, "Local Preset Overlay", 900, 760,
                                                       on_message=on_message)
            return orca.ExecutionResult.success("Opened Local Preset Overlay")

    @orca.plugin
    class LocalPresetOverlay(orca.base):
        def register_capabilities(self):
            orca.register_capability(OverlayScript)
