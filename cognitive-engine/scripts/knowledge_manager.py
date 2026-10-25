#!/usr/bin/env python3
"""Knowledge Manager V2: vault-aware, non-mutating change-set builder and review gate.

No LLM or vault writes: deterministic retrieval proposes matches; a researcher
supplies precise final page replacements via JSON proposal. Human approval is
required after all paths, content and diffs are frozen.
"""
from __future__ import annotations
import argparse
import copy
import os
import tempfile
import difflib
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEARCH = ("wiki/concepts", "wiki/entities", "wiki/analyses", "wiki/comparisons")
ALLOWED = {"create", "update", "merge", "link", "noop"}
STATES = {"pending", "approved", "rejected"}
def digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
def stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def normalized_tokens(s: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", s.lower())
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", s)
    return {w for w in words+cjk if w not in {"the","and","for","with","from","into","user","source","agent"}}
def safe_path(vault: Path, rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix != ".md":
        raise ValueError(f"Invalid target path: {rel}")
    # Existing per-domain navigation pages are part of the same reviewed change set.
    # Do not allow general Entity body updates through this narrow persistence API.
    domain_navigation = (len(candidate.parts) == 4
                         and candidate.parts[:2] == ("wiki", "entities")
                         and candidate.name == "_domain.md")
    if not (str(candidate).startswith("wiki/concepts/")
            or str(candidate).startswith("wiki/analyses/")
            or str(candidate).startswith("wiki/comparisons/")
            or str(candidate) in ("gaps.md", "index.md")
            or domain_navigation):
        raise ValueError(f"Unapproved vault surface: {rel}")
    full = (vault / candidate).resolve()
    if not full.is_relative_to(vault.resolve()):
        raise ValueError(f"Path escapes vault: {rel}")
    if domain_navigation and not full.is_file():
        raise ValueError(f"Domain navigation must already exist: {rel}")
    return full

class KnowledgeManager:
    def __init__(self, engine=ROOT, vault=None):
        self.engine = Path(engine).resolve()
        self.vault = Path(vault or self.engine.parent / "obsidian-vault").resolve()
        self.out_dir = self.engine / "runtime" / "knowledge-manager"
    def load_batches(self, paths):
        rows = []; batches = []
        for raw in paths:
            p = Path(raw)
            doc = yaml.safe_load(p.read_text("utf-8"))
            if not isinstance(doc, dict) or not isinstance(doc.get("deltas"), list):
                raise ValueError(f"Malformed delta batch: {p}")
            batches.append({"path": str(p.resolve()),"batch_id":doc.get("batch_id"),"learning_unit_id":doc.get("learning_unit_id")})
            for d in doc["deltas"]:
                if not d.get("delta_id"): raise ValueError("Delta missing ID")
                rows.append({"delta_id":d["delta_id"],"human_review_card":d.get("human_review_card"),
                             "derived_from_delta_id":d.get("derived_from_delta_id"),
                             "target":d.get("target"),"type":d.get("type"),
                             "provenance":d.get("provenance"),"trace_evidence":d.get("trace_evidence",[]),
                             "source_session_ids":d.get("source_session_ids",[]),
                             "legacy_review_status":d.get("review_status"),
                             "is_original_distiller_delta": not bool(d.get("derived_from_delta_id")),
                             "approved_body_sha256":digest(d["approved_knowledge_body"]) if d.get("approved_knowledge_body") else None})
        return batches, rows
    def catalog(self):
        output=[]
        for folder in SEARCH:
            base=self.vault / folder
            if not base.exists(): continue
            for p in sorted(base.rglob("*.md")):
                s=p.read_text("utf-8")
                output.append({"path":str(p.relative_to(self.vault)),"text":s,
                               "tokens":normalized_tokens(p.stem.replace("-"," ")+ " "+s[:1400])})
        return output
    def discover(self, batch_paths, top_k=4):
        batches, rows = self.load_batches(batch_paths)
        catalog = self.catalog()
        output=[]
        for row in rows:
            # Legacy child approvals are evidence for consolidation, not new Distiller discoveries.
            q = normalized_tokens(str(row["target"] or ""))
            ranked=[]
            for page in catalog:
                hits=q & page["tokens"]
                if hits:
                    ranked.append({"path":page["path"],"score":round(len(hits)/max(len(q),1),4),
                                   "matched_tokens":sorted(hits)[:12]})
            ranked.sort(key=lambda r:(-r["score"],r["path"]))
            output.append({**row,"candidate_matches":ranked[:top_k],
                           "decision":"needs_semantic_review","suggested_action":None})
        return {"schema_version":2,"kind":"km_discovery","created_at":stamp(),
                "vault_root":str(self.vault),"source_batches":batches,"delta_candidates":output,
                "guardrails":["Matches are retrieval candidates, not proof of semantic duplicates.",
                              "QUESTION is not evidence of mastery.",
                              "Legacy approved text is not an approved condensed vault patch.",
                              "No vault writes; explicit final patches and one Human Review required."]}
    def build(self, discovery, proposals):
        # proposals: exact final full text for target; optional noop; link provenance explicitly.
        lookup={d["delta_id"]:d for d in discovery["delta_candidates"]}
        if not proposals: raise ValueError("At least one proposal is needed")
        changed=set(); items=[]
        for proposal in proposals:
            action=proposal["action"]; rel=proposal.get("target_path")
            if action not in ALLOWED: raise ValueError("Unsupported action")
            ids=proposal.get("delta_ids") or []
            if not ids or len(set(ids))!=len(ids) or any(i not in lookup for i in ids):
                raise ValueError("Each item requires unique known delta IDs")
            if action == "noop":
                if rel: raise ValueError("noop cannot select a target path")
                items.append({"change_id":uuid.uuid4().hex,"action":action,"delta_ids":ids,
                              "reason":proposal.get("reason",""),"status":"pending"})
                continue
            if rel in changed: raise ValueError("A target can only be changed once per Change Set")
            changed.add(rel)
            target=safe_path(self.vault, rel)
            before=target.read_text("utf-8") if target.exists() else None
            if action=="create" and before is not None: raise ValueError("create target already exists")
            if action!="create" and before is None: raise ValueError("update target does not exist")
            after=proposal.get("after")
            if not isinstance(after,str) or not after.strip() or not after.endswith("\n"):
                raise ValueError("Full proposed markdown must be nonempty and end with newline")
            if action=="create" and not after.lstrip().startswith("---\n"):
                raise ValueError("New concept must have YAML frontmatter")
            if before == after: raise ValueError("No actual change; use noop")
            # Caution: this is full replacement text for a precise diff, not an instruction to blindly append.
            diff="\n".join(difflib.unified_diff((before or "").splitlines(),after.splitlines(),
                fromfile="a/"+rel if before is not None else "/dev/null",tofile="b/"+rel,lineterm=""))+"\n"
            items.append({"change_id":uuid.uuid4().hex,"action":action,"target_path":rel,
                          "before_sha256":digest(before) if before is not None else None,
                          "after_sha256":digest(after),"after":after,"diff":diff,
                          "delta_ids":ids,"reason":proposal.get("reason",""),
                          "source_evidence":[{"delta_id":i,"type":lookup[i].get("type"),"provenance":lookup[i].get("provenance"),
                                              "trace_evidence":lookup[i].get("trace_evidence",[]),
                                              "source_session_ids":lookup[i].get("source_session_ids",[]),
                                              "legacy_review_status":lookup[i].get("legacy_review_status"),
                                              "approved_body_sha256":lookup[i].get("approved_body_sha256")} for i in ids],
                          "provenance_review_required":any(lookup[i]["type"] in ("QUESTION","INSIGHT_CANDIDATE") for i in ids),
                          "status":"pending"})
        immutable={"schema_version":2,"kind":"knowledge_change_set","vault_root":str(self.vault),
                   "learning_unit_ids":sorted({r["learning_unit_id"] for r in discovery["source_batches"] if r["learning_unit_id"]}),
                   "source_batch_ids":[b["batch_id"] for b in discovery["source_batches"]],
                   "changes":items}
        immutable["change_set_hash"]=digest(json.dumps(immutable,sort_keys=True,ensure_ascii=False))
        return {**immutable,"change_set_id":uuid.uuid4().hex,"created_at":stamp(),
                "review_status":"pending","review_history":[],
                "warning":"Draft only. Neither approved nor committed; no vault was modified."}
    def verify(self, cs, enforce_approval=False):
        if cs.get("kind")!="knowledge_change_set": raise ValueError("Not a Change Set")
        frozen=copy.deepcopy({k:cs[k] for k in ("schema_version","kind","vault_root","learning_unit_ids","source_batch_ids","changes")})
        for item in frozen["changes"]: item["status"]="pending"
        if digest(json.dumps(frozen,sort_keys=True,ensure_ascii=False))!=cs["change_set_hash"]:
            raise ValueError("Change Set tampered with after construction")
        if Path(cs["vault_root"]).resolve()!=self.vault: raise ValueError("Vault root mismatch")
        # Status cannot be edited directly: it must follow the recorded one-time decisions.
        states={}
        for item in cs["changes"]:
            cid=item["change_id"]
            if cid in states: raise ValueError("Duplicate change ID")
            states[cid]="pending"
        history=cs.get("review_history",[])
        if not isinstance(history,list): raise ValueError("Invalid review history")
        for event in history:
            ids=event.get("change_ids",[])
            choice=event.get("decision")
            if event.get("approved_hash")!=cs["change_set_hash"] or choice not in ("approve","reject"):
                raise ValueError("Invalid approval history or version hash")
            if not isinstance(ids,list) or not ids or len(set(ids))!=len(ids):
                raise ValueError("Invalid reviewed IDs")
            for cid in ids:
                if cid not in states or states[cid]!="pending":
                    raise ValueError("Replayed or unknown review decision")
                states[cid]="approved" if choice=="approve" else "rejected"
        for item in cs["changes"]:
            if item["status"]!=states[item["change_id"]]:
                raise ValueError("Review status lacks matching approval history")
        observed=set(states.values())
        expected="pending" if "pending" in observed else ("approved" if observed=={"approved"} else "reviewed")
        if cs.get("review_status")!=expected: raise ValueError("Review status summary mismatch")
        for item in cs["changes"]:
            if enforce_approval and item["status"]!="approved": continue
            if item["action"]=="noop": continue
            p=safe_path(self.vault,item["target_path"])
            current=p.read_text("utf-8") if p.exists() else None
            if (digest(current) if current is not None else None)!=item["before_sha256"]:
                raise ValueError("Vault target changed: "+item["target_path"])
            if digest(item["after"])!=item["after_sha256"]:
                raise ValueError("Proposed body hash mismatch")
        return True
    def review(self, cs, decision, change_ids=None):
        if decision not in ("approve","reject"): raise ValueError("Expected approve or reject")
        self.verify(cs)
        ids=set(change_ids or [x["change_id"] for x in cs["changes"]])
        if not ids or not ids.issubset({i["change_id"] for i in cs["changes"]}):
            raise ValueError("Unknown change IDs")
        for item in cs["changes"]:
            if item["change_id"] in ids:
                if item["status"]!="pending": raise ValueError("Already reviewed; cannot overwrite decision")
                item["status"]="approved" if decision=="approve" else "rejected"
        cs["review_history"].append({"at":stamp(),"decision":decision,
                                      "change_ids":sorted(ids),"approved_hash":cs["change_set_hash"]})
        statuses={x["status"] for x in cs["changes"]}
        cs["review_status"]="pending" if "pending" in statuses else ("approved" if statuses=={"approved"} else "reviewed")
        self.verify(cs)
        return cs
    @staticmethod
    def _atomic_text(path, content):
        """Replace one file atomically; only called by an explicitly approved commit."""
        fd,tmp=tempfile.mkstemp(prefix=".km-",dir=str(path.parent))
        try:
            with os.fdopen(fd,"w",encoding="utf-8",newline="") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def commit(self, cs, *, apply=False):
        """Single-review executor. Preview by default; never self-approves."""
        self.verify(cs,enforce_approval=True)
        if any(i["status"]=="pending" for i in cs["changes"]):
            raise ValueError("All Change Set items must be reviewed before commit")
        if not cs.get("review_history"):
            raise ValueError("Missing explicit review record")
        cid=str(uuid.UUID(cs["change_set_id"]))
        audit_dir=self.out_dir/"commits"
        audit_path=audit_dir/(cid+".json")
        if audit_path.exists(): raise ValueError("Change Set already committed")
        selected=[i for i in cs["changes"] if i["status"]=="approved" and i["action"]!="noop"]
        summary={"change_set_id":cid,"change_set_hash":cs["change_set_hash"],
                 "target_paths":[i["target_path"] for i in selected],
                 "applied":False,"approved_count":len(selected),
                 "rejected_count":sum(i["status"]=="rejected" for i in cs["changes"])}
        if not apply: return summary
        if not selected: raise ValueError("No approved file updates to apply")
        # Complete preflight before any writes; keep reversible preimages outside the Vault.
        targets=[]
        for item in selected:
            target=safe_path(self.vault,item["target_path"])
            old=target.read_text("utf-8") if target.exists() else None
            if (digest(old) if old is not None else None)!=item["before_sha256"]:
                raise ValueError("Vault drift before commit: "+item["target_path"])
            targets.append((item,target,old))
        rollback_dir=self.out_dir/"rollback"/cid
        if rollback_dir.exists(): raise ValueError("Rollback directory already exists")
        rollback_dir.mkdir(parents=True)
        for item,target,old in targets:
            if old is not None:
                recovery=rollback_dir/item["target_path"]
                recovery.parent.mkdir(parents=True,exist_ok=True)
                recovery.write_text(old,"utf-8")
        changed=[]
        made_dirs=[]
        try:
            for item,target,old in targets:
                absent=[]
                d=target.parent
                while not d.exists():
                    absent.append(d)
                    d=d.parent
                for d in reversed(absent):
                    d.mkdir()
                    made_dirs.append(d)
                self._atomic_text(target,item["after"])
                changed.append((target,old))
            audit={"kind":"knowledge_commit","at":stamp(),
                   "change_set_id":cid,"change_set_hash":cs["change_set_hash"],
                   "changes":[{"change_id":i["change_id"],"target_path":i["target_path"],
                               "delta_ids":i["delta_ids"],"before_sha256":i["before_sha256"],
                               "after_sha256":i["after_sha256"]} for i in selected],
                   "rollback_dir":str(rollback_dir),
                   "note":"Approval provenance recorded in Change Set. Vault Git commit is a separate controlled step."}
            audit_dir.mkdir(parents=True,exist_ok=True)
            self._atomic_text(audit_path,json.dumps(audit,ensure_ascii=False,indent=2)+"\n")
        except Exception:
            for target,old in reversed(changed):
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    self._atomic_text(target,old)
            for d in reversed(made_dirs):
                try: d.rmdir()
                except OSError: pass
            raise
        summary["applied"]=True
        summary["audit_path"]=str(audit_path)
        summary["rollback_dir"]=str(rollback_dir)
        return summary

    def save(self, data, name):
        self.out_dir.mkdir(parents=True,exist_ok=True)
        p=self.out_dir / name
        p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n","utf-8")
        return p

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--engine-root",default=str(ROOT))
    ap.add_argument("--vault-root")
    sub=ap.add_subparsers(dest="command",required=True)
    d=sub.add_parser("discover");d.add_argument("--batches",nargs="+",required=True);d.add_argument("--output",required=True)
    b=sub.add_parser("build");b.add_argument("--discovery",required=True);b.add_argument("--proposals",required=True);b.add_argument("--output",required=True)
    v=sub.add_parser("verify");v.add_argument("--change-set",required=True)
    r=sub.add_parser("review");r.add_argument("--change-set",required=True);r.add_argument("--decision",choices=["approve","reject"],required=True);r.add_argument("--change-ids",nargs="*")
    c=sub.add_parser("commit");c.add_argument("--change-set",required=True);c.add_argument("--apply",action="store_true",help="Apply only an explicitly reviewed Change Set; default is read-only preview")
    a=ap.parse_args();km=KnowledgeManager(a.engine_root,a.vault_root)
    if a.command=="discover":out=km.discover(a.batches);path=Path(a.output)
    elif a.command=="build":
        disc=json.loads(Path(a.discovery).read_text("utf-8"))
        out=km.build(disc,json.loads(Path(a.proposals).read_text("utf-8")));path=Path(a.output)
    elif a.command=="verify":
        cs=json.loads(Path(a.change_set).read_text("utf-8"));km.verify(cs)
        print(json.dumps({"valid":True,"changes":len(cs["changes"]),"status":cs["review_status"]}));return
    elif a.command=="commit":
        cs=json.loads(Path(a.change_set).read_text("utf-8"))
        print(json.dumps(km.commit(cs,apply=a.apply),ensure_ascii=False));return
    else:
        path=Path(a.change_set);cs=json.loads(path.read_text("utf-8"))
        out=km.review(cs,a.decision,a.change_ids)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n","utf-8")
    print(json.dumps({"output":str(path),"changes":len(out.get("changes",[])),"candidates":len(out.get("delta_candidates",[])),"review_status":out.get("review_status")}))
if __name__=="__main__":main()
