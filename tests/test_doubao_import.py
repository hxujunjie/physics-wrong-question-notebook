import unittest
from src import doubao_import, recognition_import

class LegacyDoubaoImportCompatTests(unittest.TestCase):
    def test_reexports_recognition_import(self):
        self.assertIs(doubao_import.import_recognition_result, recognition_import.import_recognition_result)
        self.assertIs(doubao_import.validate_recognition, recognition_import.validate_recognition)

if __name__ == "__main__":
    unittest.main()
