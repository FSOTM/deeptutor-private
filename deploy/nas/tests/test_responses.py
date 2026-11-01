import copy
import unittest

from deeptutor.capabilities.mastery.tools import MasteryQuizTool
from deeptutor.services.llm.provider_core.openai_responses.converters import convert_tools


class ResponsesTests(unittest.TestCase):
    def test_optional_quiz_fields_remain_optional(self):
        tool=MasteryQuizTool().get_definition().to_openai_schema()
        original=copy.deepcopy(tool)
        converted=convert_tools([tool])[0]
        self.assertIs(converted['strict'],False)
        self.assertEqual(converted['parameters'],tool['function']['parameters'])
        self.assertNotIn('resume_question_id',converted['parameters'].get('required',[]))
        self.assertEqual(tool,original)

    def test_explicit_strict_is_respected(self):
        for strict in [True,False]:
            tool=MasteryQuizTool().get_definition().to_openai_schema()
            tool['function']['strict']=strict
            self.assertIs(convert_tools([tool])[0]['strict'],strict)

    def test_native_search_is_preserved(self):
        self.assertEqual(convert_tools([{'type':'function','function':{'name':'web_search'}}],native_web_search=True),[{'type':'web_search'}])


if __name__=='__main__':
    unittest.main()
