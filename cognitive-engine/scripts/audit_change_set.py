#!/usr/bin/env python3
"""Verify proposed V2 Change Set trace provenance and flag unsourced factual prose.

Read-only: checks frozen Change Set, all cited trace references, original/derived
delta identities, and embedded source citations in the final proposed content.
Never treats USER questions as evidence of mastery or factual confirmation.
"""
from __future__ import annotations
import argparse
import json
import re
from collections import Counter
from pathlib import Path
import yaml
from knowledge_manager import KnowledgeManager

TRACE_REF = re.compile(r"^([a-z_]+)\[(\d+)\]$")
SOURCE_RE = re.compile(r"https?://|raw/sources/|wiki/summaries/|(?:Lecture|Slide|Chapter|Ch\.)\s*\d+",re.IGNORECASE)

def audit(change_set:dict, discovery:dict, engine:Path, vault:Path) -> dict:
    km=KnowledgeManager(engine,vault)
    km.verify(change_set)
    trace_by_session={}
    original={}
    derived={}
    for batch in discovery["source_batches"]:
        doc=yaml.safe_load(Path(batch["path"]).read_text("utf-8"))
        for delta in doc["deltas"]:
            did=delta["delta_id"]
            (derived if delta.get("derived_from_delta_id") else original)[did]=delta
            for session in delta.get("source_session_ids",[]):
                if session not in trace_by_session:
                    p=engine/"cognitive-inbox"/"processed"/(session+".yaml")
                    trace_by_session[session]=yaml.safe_load(p.read_text("utf-8")) if p.exists() else None
    findings=[]
    mapped=[]
    for change in change_set["changes"]:
        linked=[]
        for e in change.get("source_evidence",[]):
            did=e["delta_id"]
            record=original.get(did) or derived.get(did)
            if record is None:
                findings.append({"severity":"error","path":change.get("target_path"),"delta_id":did,"issue":"delta_not_in_original_batch"})
                continue
            if record.get("provenance")!=e.get("provenance"):
                findings.append({"severity":"error","path":change.get("target_path"),"delta_id":did,"issue":"provenance_mismatch"})
            if record.get("trace_evidence",[])!=e.get("trace_evidence",[]):
                findings.append({"severity":"error","path":change.get("target_path"),"delta_id":did,"issue":"trace_evidence_mismatch"})
            for ref in e.get("trace_evidence",[]):
                match=TRACE_REF.fullmatch(str(ref))
                if not match:
                    # Legacy V1 cards sometimes saved the actual USER utterance
                    # rather than a user_questions[N] pointer. Accept ONLY an
                    # exact content match in the referenced source session.
                    exact_matches=[]
                    for sid in e.get("source_session_ids",[]):
                        trace=trace_by_session.get(sid)
                        if not isinstance(trace,dict): continue
                        for key in ("user_questions","user_reasoning","user_corrections","user_connections","user_confirmations"):
                            for idx,item in enumerate(trace.get(key,[]) or []):
                                content=item.get("content","") if isinstance(item,dict) else str(item)
                                if str(content).strip()==str(ref).strip():
                                    exact_matches.append({"session_id":sid,"trace_ref":f"{key}[{idx}]"})
                    if not exact_matches:
                        findings.append({"severity":"warning","path":change.get("target_path"),"delta_id":did,"issue":"unstructured_trace_evidence","ref":ref})
                    continue
                key,idx=match.group(1),int(match.group(2))
                ok=False
                for sid in e.get("source_session_ids",[]):
                    trace=trace_by_session.get(sid)
                    values=trace.get(key,[]) if isinstance(trace,dict) else []
                    if isinstance(values,list) and idx<len(values):
                        ok=True
                if not ok:
                    findings.append({"severity":"error","path":change.get("target_path"),"delta_id":did,"issue":"trace_reference_not_found","ref":ref})
            linked.append(did)
        if change["action"]!="noop":
            content=change.get("after","")
            if not SOURCE_RE.search(content):
                findings.append({"severity":"warning","path":change["target_path"],"issue":"no_precise_source_citation_in_final_page"})
            if not linked:
                findings.append({"severity":"error","path":change["target_path"],"issue":"no_linked_delta_evidence"})
        mapped.extend(linked)
    all_original=set(original)
    covered=set(mapped)
    if all_original-covered:
        findings.append({"severity":"error","issue":"unmapped_original_distiller_deltas","delta_ids":sorted(all_original-covered)})
    issues=Counter(x["severity"] for x in findings)
    return {
       "change_set_id":change_set["change_set_id"],"change_set_hash":change_set["change_set_hash"],
       "source_batches":len(discovery["source_batches"]), "original_distiller_deltas":len(original),
       "derived_review_records":len(derived),"changes":len(change_set["changes"]),
       "covered_unique_delta_ids":len(covered),
       "trace_sessions_checked":len(trace_by_session),
       "errors":issues["error"],"warnings":issues["warning"],"findings":findings,
       "verified_scope":"Trace-reference existence and metadata consistency, NOT factual truth or source corroboration",
       "ready_for_fact_review":issues["error"]==0,
       "ready_for_commit":False,
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--change-set",required=True)
    p.add_argument("--discovery",required=True)
    p.add_argument("--engine-root",default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--vault-root")
    p.add_argument("--output")
    p.add_argument("--strict",action="store_true")
    a=p.parse_args()
    engine=Path(a.engine_root).resolve()
    vault=Path(a.vault_root or engine.parent/"obsidian-vault").resolve()
    cs=json.loads(Path(a.change_set).read_text("utf-8"))
    discovery=json.loads(Path(a.discovery).read_text("utf-8"))
    result=audit(cs,discovery,engine,vault)
    if a.output:
        target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="findings"},ensure_ascii=False,indent=2))
    if a.strict and (result["errors"] or result["warnings"]):
        raise SystemExit(2)
if __name__=="__main__":main()
