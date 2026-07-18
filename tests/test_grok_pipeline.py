import unittest
from src import grok_pipeline, recognition_pipeline

class LegacyGrokPipelineCompatTests(unittest.TestCase):
    def test_reexports_recognition_pipeline(self):
        self.assertIs(grok_pipeline.RecognitionJob, recognition_pipeline.RecognitionJob)
        self.assertIs(grok_pipeline.preflight, recognition_pipeline.preflight)

if __name__ == "__main__":
    unittest.main()
