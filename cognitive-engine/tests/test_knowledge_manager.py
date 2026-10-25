import importlib.util
import tempfile
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location("knowledge_manager",Path(__file__).resolve().parents[1]/"scripts"/"knowledge_manager.py")
km_mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(km_mod)

class KnowledgeManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.engine=root/"engine";self.vault=root/"vault"
        p=self.vault/"wiki"/"concepts"/"finance"/"duration.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\ntitle: Duration\nnote_type: concept\ndomain: finance\n---\n# Duration\n\nOld\n")
        self.km=km_mod.KnowledgeManager(self.engine,self.vault)
        self.discovery={"source_batches":[{"batch_id":"b","learning_unit_id":"fin3703a"}],
                        "delta_candidates":[{"delta_id":"d","target":"Duration","type":"QUESTION"}]}
        self.target="wiki/concepts/finance/duration.md"
        self.before=p.read_text()
        self.after=self.before.replace("Old","New")
    def tearDown(self):self.tmp.cleanup()
    def test_final_diff_hash_and_single_review(self):
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,"delta_ids":["d"],"after":self.after}])
        self.assertTrue(self.km.verify(cs))
        self.assertEqual(cs["review_status"],"pending")
        self.km.review(cs,"approve")
        self.assertEqual(cs["review_status"],"approved")
        self.assertTrue(self.km.verify(cs))
        with self.assertRaises(ValueError):self.km.review(cs,"approve")
        self.assertEqual((self.vault/self.target).read_text(),self.before)
    def test_detects_source_drift(self):
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,"delta_ids":["d"],"after":self.after}])
        (self.vault/self.target).write_text("outside edit\n")
        with self.assertRaisesRegex(ValueError,"changed"):self.km.review(cs,"approve")
    def test_tamper_fails(self):
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,"delta_ids":["d"],"after":self.after}])
        cs["changes"][0]["after"]=self.after+"injected"
        with self.assertRaisesRegex(ValueError,"tampered"):self.km.verify(cs)
    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.km.build(self.discovery,[{"action":"create","target_path":"wiki/concepts/../../bad.md","delta_ids":["d"],"after":"---\ntitle: T\n---\n"}])
    def test_create_requires_new_path_and_frontmatter(self):
        with self.assertRaises(ValueError):
            self.km.build(self.discovery,[{"action":"create","target_path":self.target,"delta_ids":["d"],"after":self.after}])
        cs=self.km.build(self.discovery,[{"action":"create","target_path":"wiki/concepts/finance/new.md","delta_ids":["d"],"after":"---\ntitle: New\nnote_type: concept\ndomain: finance\n---\n# New\n"}])
        self.assertTrue(self.km.verify(cs))
    def test_existing_domain_navigation_can_join_one_reviewed_commit(self):
        domain="wiki/entities/finance/_domain.md"
        nav=self.vault/domain
        nav.parent.mkdir(parents=True,exist_ok=True)
        nav.write_text("---\ntitle: Finance\nnote_type: domain\n---\n# Finance\n",encoding="utf-8")
        after_nav=nav.read_text(encoding="utf-8")+"\n- [[wiki/concepts/finance/duration|Duration]]\n"
        cs=self.km.build(self.discovery,[
            {"action":"update","target_path":self.target,"delta_ids":["d"],"after":self.after},
            {"action":"link","target_path":domain,"delta_ids":["d"],"after":after_nav}])
        self.assertTrue(self.km.verify(cs))
        with self.assertRaisesRegex(ValueError,"reviewed"):
            self.km.commit(cs,apply=True)
        self.assertEqual(nav.read_text(encoding="utf-8"),"---\ntitle: Finance\nnote_type: domain\n---\n# Finance\n")
        self.km.review(cs,"approve")
        out=self.km.commit(cs,apply=True)
        self.assertTrue(out["applied"])
        self.assertEqual(out["approved_count"],2)
        self.assertEqual(nav.read_text(encoding="utf-8"),after_nav)
        self.assertEqual((Path(out["rollback_dir"])/domain).read_text(encoding="utf-8"),
                         "---\ntitle: Finance\nnote_type: domain\n---\n# Finance\n")

    def test_domain_navigation_restricts_to_existing_domain_index(self):
        entity="wiki/entities/finance/unauthorized-entity.md"
        p=self.vault/entity
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text("# Entity\n",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"Unapproved vault surface"):
            self.km.build(self.discovery,[{"action":"update","target_path":entity,
              "delta_ids":["d"],"after":"# Changed\n"}])
        with self.assertRaisesRegex(ValueError,"must already exist"):
            self.km.build(self.discovery,[{"action":"create",
              "target_path":"wiki/entities/finance/_domain.md",
              "delta_ids":["d"],"after":"---\ntitle: Domain\n---\n# Domain\n"}])

    def test_noop_and_reject(self):
        cs=self.km.build(self.discovery,[{"action":"noop","delta_ids":["d"],"reason":"duplicate"}])
        self.km.review(cs,"reject")
        self.assertEqual(cs["review_status"],"reviewed")
        self.assertTrue(self.km.verify(cs))

    def test_commit_preview_is_read_only(self):
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,
                                        "delta_ids":["d"],"after":self.after}])
        self.km.review(cs,"approve")
        result=self.km.commit(cs)
        self.assertFalse(result["applied"])
        self.assertEqual((self.vault/self.target).read_text(),self.before)
        self.assertFalse((self.engine/"runtime").exists())

    def test_direct_status_forgery_rejected(self):
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,
                                        "delta_ids":["d"],"after":self.after}])
        cs["changes"][0]["status"]="approved"
        cs["review_status"]="approved"
        with self.assertRaisesRegex(ValueError,"approval history"):
            self.km.commit(cs,apply=True)
        self.assertEqual((self.vault/self.target).read_text(),self.before)

    def test_commit_requires_final_review(self):
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,
                                        "delta_ids":["d"],"after":self.after}])
        with self.assertRaisesRegex(ValueError,"reviewed"):
            self.km.commit(cs,apply=True)
        self.assertEqual((self.vault/self.target).read_text(),self.before)

    def test_commit_synthetic_vault_with_audit_and_recovery(self):
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,
                                        "delta_ids":["d"],"after":self.after}])
        self.km.review(cs,"approve")
        result=self.km.commit(cs,apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual((self.vault/self.target).read_text(),self.after)
        self.assertEqual((Path(result["rollback_dir"])/self.target).read_text(),self.before)
        self.assertTrue(Path(result["audit_path"]).exists())
        with self.assertRaises(ValueError):self.km.commit(cs,apply=True)

    def test_commit_skips_rejected_items(self):
        second="wiki/concepts/finance/new.md"
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,
                                         "delta_ids":["d"],"after":self.after},
                                        {"action":"create","target_path":second,"delta_ids":["d"],
                                         "after":"---\ntitle: New\n---\n# New\n"}])
        self.km.review(cs,"approve",[cs["changes"][0]["change_id"]])
        self.km.review(cs,"reject",[cs["changes"][1]["change_id"]])
        result=self.km.commit(cs,apply=True)
        self.assertEqual(result["approved_count"],1)
        self.assertEqual((self.vault/self.target).read_text(),self.after)
        self.assertFalse((self.vault/second).exists())

    def test_commit_rolls_back_on_write_failure(self):
        second="wiki/concepts/finance/new.md"
        cs=self.km.build(self.discovery,[{"action":"update","target_path":self.target,
                                         "delta_ids":["d"],"after":self.after},
                                        {"action":"create","target_path":second,"delta_ids":["d"],
                                         "after":"---\ntitle: New\n---\n# New\n"}])
        self.km.review(cs,"approve")
        original=self.km._atomic_text
        count=[0]
        def interrupted(path,body):
            count[0]+=1
            if count[0]==2:raise OSError("synthetic interruption")
            return original(path,body)
        self.km._atomic_text=interrupted
        with self.assertRaisesRegex(OSError,"synthetic interruption"):
            self.km.commit(cs,apply=True)
        self.assertEqual((self.vault/self.target).read_text(),self.before)
        self.assertFalse((self.vault/second).exists())
        self.assertFalse((self.km.out_dir/"commits"/(str(km_mod.uuid.UUID(cs["change_set_id"]))+".json")).exists())


if __name__=="__main__":unittest.main()
