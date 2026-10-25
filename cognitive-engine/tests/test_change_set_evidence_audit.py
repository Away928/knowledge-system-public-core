import json
import sys
import tempfile
import unittest
from pathlib import Path
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from knowledge_manager import KnowledgeManager
from audit_change_set import audit

class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        home=Path(self.t.name);self.engine=home/"engine";self.vault=home/"vault"
        self.engine.mkdir();self.vault.mkdir()
        self.sid="session1"
        p=self.engine/"cognitive-inbox/processed/session1.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(yaml.safe_dump({"session_id":self.sid,"user_questions":[{"content":"what is interest rate risk","provenance":"USER"}]},allow_unicode=True))
        self.original={"delta_id":"parent","target":"rates","type":"QUESTION","provenance":"USER",
                       "trace_evidence":["user_questions[0]"],"source_session_ids":[self.sid],"review_status":"pending"}
        self.child={"delta_id":"child","human_review_card":"KD-01","derived_from_delta_id":"parent",
                    "target":"rates","type":"NEW","provenance":"MIXED","trace_evidence":["user_questions[0]"],
                    "source_session_ids":[self.sid],"review_status":"approved"}
        batch={"batch_id":"b","learning_unit_id":"fin","deltas":[self.original,self.child]}
        self.path=home/"batch.yaml";self.path.write_text(yaml.safe_dump(batch))
        self.km=KnowledgeManager(self.engine,self.vault)
        self.discovery=self.km.discover([self.path])
        note="---\ntitle: Rates\nnote_type: concept\ndomain: finance\n---\n# Rates\n\nRates change.\n"
        self.proposals=[{"action":"create","target_path":"wiki/concepts/finance/rates.md",
                         "delta_ids":["parent","child"],"after":note}]
    def tearDown(self):self.t.cleanup()
    def test_grounds_original_vs_derived_and_warns_unsourced(self):
        cs=self.km.build(self.discovery,self.proposals)
        result=audit(cs,self.discovery,self.engine,self.vault)
        self.assertEqual(result["original_distiller_deltas"],1)
        self.assertEqual(result["derived_review_records"],1)
        self.assertEqual(result["errors"],0)
        self.assertEqual(result["warnings"],1)
        self.assertFalse(result["ready_for_commit"])
    def test_invalid_trace_ref_blocks(self):
        self.child["trace_evidence"]=["user_questions[5]"]
        batch=yaml.safe_load(self.path.read_text())
        batch["deltas"][1]["trace_evidence"]=["user_questions[5]"]
        self.path.write_text(yaml.safe_dump(batch))
        self.discovery=self.km.discover([self.path])
        cs=self.km.build(self.discovery,self.proposals)
        result=audit(cs,self.discovery,self.engine,self.vault)
        self.assertEqual(result["errors"],1)
        self.assertEqual(result["findings"][0]["issue"],"trace_reference_not_found")
    def test_unstructured_history_flagged_not_destroyed(self):
        batch=yaml.safe_load(self.path.read_text())
        batch["deltas"][1]["trace_evidence"]=["what is interest rate risk"]
        self.path.write_text(yaml.safe_dump(batch))
        self.discovery=self.km.discover([self.path])
        cs=self.km.build(self.discovery,self.proposals)
        result=audit(cs,self.discovery,self.engine,self.vault)
        self.assertEqual(result["errors"],0)
        self.assertNotIn("unstructured_trace_evidence",{f["issue"] for f in result["findings"]})
        # A non-matching historical free-form quote still produces a warning.
        batch=yaml.safe_load(self.path.read_text())
        batch["deltas"][1]["trace_evidence"]=["unsupported quote"]
        self.path.write_text(yaml.safe_dump(batch))
        self.discovery=self.km.discover([self.path])
        cs=self.km.build(self.discovery,self.proposals)
        result=audit(cs,self.discovery,self.engine,self.vault)
        self.assertIn("unstructured_trace_evidence",{f["issue"] for f in result["findings"]})

if __name__=="__main__":unittest.main()
