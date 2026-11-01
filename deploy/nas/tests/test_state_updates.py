"""State transport tests: immutable prefixes, exact recovery and old sessions."""
import copy
import json
import sys
import unittest
from types import SimpleNamespace as NS
from test_policy import updates as u, model_history, mastery, pipeline_module, mastery_pipeline


def state(revision=1, score=0, brief=False, path='test-course', mode='study'):
    result = {'status':'active','path_id':path,'mode':mode,'path_revision':revision,
              'goal':'Learn English with one example at a time. '*20,
              'next':{'action':'learn','knowledge_point_id':'kp-1','mastery':score},
              'map':{'modules':[{'id':'module-1','knowledge_points':[
                  {'id':'kp-'+str(i),'mastery':score if i==1 else 0,
                   'name':'Practice past tense with a different example. '*15}
                  for i in range(1,23)]}]},
              'learner_profile':{'preferences':'Chinese explanations; preserve exact learner answers.'},
              'intake_needed':False}
    if brief:result['state_scope']='working_brief'
    return result


def snapshot(payload):
    return {'role':'user','_context_snapshot':'mastery_status_snapshot',
            'content':model_history._MASTERY_SNAPSHOT_NOTICE+json.dumps(payload,ensure_ascii=False)}


def tool(payload, ident='status-1'):
    return [{'role':'assistant','content':'','tool_calls':[
        {'id':ident,'type':'function','function':{'name':'mastery_status','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':ident,'name':'mastery_status',
         'content':json.dumps(payload,ensure_ascii=False)}]


def envelope(message):
    content=message['content']
    return json.loads(content[content.index('{'):])['mastery_state']


class StateTests(unittest.TestCase):
    def test_migration_preserves_the_original_system_prompt(self):
        from unittest.mock import patch
        host=mastery_pipeline.MasteryLoopPipeline.__new__(mastery_pipeline.MasteryLoopPipeline)
        host._runtime_snapshots={};host._prompt_assembler=NS()
        host._can_use_native_tool_calling=lambda:False
        context=NS(extension=lambda name:{'status_snapshot':'current snapshot'})
        with patch.object(pipeline_module.AgenticLoopPipeline,'_build_system_prompt',return_value='original system'):
            result=host._build_system_prompt([],context)
        self.assertEqual(result,'original system'+mastery_pipeline.STATE_REFERENCE_NOTICE+mastery_pipeline.MEMORY_NOTICE)
        self.assertEqual(host._runtime_snapshots['mastery_state_protocol'],u.STATE_UPDATE_NOTICE.strip())
        self.assertEqual(host._runtime_snapshots['mastery_status_snapshot'],'current snapshot')

    def test_only_new_messages_are_frozen_and_raw_content_survives(self):
        old=snapshot(state(brief=True));rows=[old,{'role':'user','content':'existing words'}]
        before=copy.deepcopy(rows)
        rows.append(snapshot(state(brief=True)))
        u.freeze_new_messages(rows,2)
        self.assertEqual(rows[:2],before)
        self.assertNotIn(u.PROJECTION_KEY,old)
        self.assertEqual(rows[-1]['content'],old['content'])
        projected=u.project_messages(rows)
        self.assertEqual(envelope(projected[-1])['format'],'unchanged')
        self.assertNotIn(u.PROJECTION_KEY,projected[-1])

    def test_many_rounds_keep_every_previous_byte_and_metadata(self):
        rows=[snapshot(state(brief=True))]
        u.freeze_new_messages(rows,0)
        for i in range(2,9):
            before=copy.deepcopy(rows);sent=copy.deepcopy(u.project_messages(rows))
            start=len(rows)
            rows.extend([{'role':'user','content':f'Round {i}'},snapshot(state(i,i/20,True))])
            u.freeze_new_messages(rows,start)
            projected=u.project_messages(rows)
            self.assertEqual(rows[:start],before)
            self.assertEqual(projected[:start],sent)

    def test_full_tool_updates_are_exact_and_small(self):
        a=state();b=state(2,0.2)
        rows=tool(a);u.freeze_new_messages(rows,0)
        start=len(rows);rows.extend(tool(b,'status-2'));u.freeze_new_messages(rows,start)
        projected=u.project_messages(rows);update=envelope(projected[-1])
        self.assertEqual(update['format'],'delta')
        self.assertEqual(u.apply_changes(a,update['changes']),b)
        self.assertEqual(update['current']['next'],b['next'])
        self.assertLess(len(projected[-1]['content']),len(rows[-1]['content'])/4)

    def test_pending_question_replaced_and_cleared_explicitly(self):
        a=state();a['pending_interaction']={'question_id':'q-1','answers':{'a':'learner text'}}
        b=state(2);b['pending_interaction']={'question_id':'q-2','answers':{}}
        c=state(3)
        rows=tool(a);u.freeze_new_messages(rows,0)
        for i,payload in enumerate((b,c),2):
            start=len(rows);rows.extend(tool(payload,f'status-{i}'));u.freeze_new_messages(rows,start)
            update=envelope(u.project_messages(rows)[-1])
            self.assertEqual(update['current']['pending_interaction'],payload.get('pending_interaction'))
            self.assertEqual(u.apply_changes(a,update['changes']),payload)
            a=payload

    def test_remove_null_arrays_and_pointer_escaping(self):
        cases=[({'a':1,'gone':3},{'a':None,'new':False}),
               ({'x':[1,2]},{'x':[2,1]}),({'x':[1,2]},{'x':[]}),
               ({'a/b':{'~x':1}},{'a/b':{'~x':2}}),({'x':True},{'x':1})]
        for a,b in cases:self.assertEqual(u.canonical(u.apply_changes(a,u.changes(a,b))),u.canonical(b))

    def test_scopes_paths_and_modes_never_share_a_base(self):
        rows=[snapshot(state(brief=True))];u.freeze_new_messages(rows,0)
        for i,payload in enumerate((state(),state(path='different'),state(mode='review')),1):
            start=len(rows);rows.extend(tool(payload,f'status-{i}'));u.freeze_new_messages(rows,start)
            self.assertEqual(envelope(u.project_messages(rows)[-1])['format'],'full')

    def test_compaction_missing_base_rehydrates_and_next_round_is_stable(self):
        rows=[snapshot(state(brief=True))];u.freeze_new_messages(rows,0)
        for i in range(2,5):
            start=len(rows);rows.append(snapshot(state(i,i/20,True)));u.freeze_new_messages(rows,start)
        survivor=copy.deepcopy(rows[-1:]);original=copy.deepcopy(survivor)
        first=u.project_messages(survivor)
        self.assertEqual(envelope(first[0])['format'],'full')
        self.assertEqual(envelope(first[0])['data'],state(4,0.2,True))
        self.assertEqual(survivor,original)
        survivor.append(snapshot(state(5,0.25,True)));u.freeze_new_messages(survivor,1)
        self.assertEqual(u.project_messages(survivor)[:1],first)
        self.assertEqual(envelope(u.project_messages(survivor)[-1])['format'],'delta')

    def test_old_plain_session_history_replays_unchanged(self):
        rows=[snapshot(state(brief=True)),*tool(state()),{'role':'assistant','content':'old explanation'}]
        before=copy.deepcopy(rows)
        self.assertEqual(u.project_messages(rows),before)
        self.assertEqual(rows,before)

    def test_durable_normalization_and_replay_keep_frozen_representation(self):
        rows=[snapshot(state(brief=True)),{'role':'user','content':'question'},
              {'role':'assistant','content':'explanation'}]
        u.freeze_new_messages(rows,0)
        normalized=model_history.normalize_model_turn({'version':1,'messages':rows})
        self.assertEqual(normalized['messages'],rows)
        dbrows=[{'id':1,'role':'user','content':'question'},
                {'id':2,'role':'assistant','content':'explanation','metadata':{'model_turn':normalized}}]
        self.assertEqual(u.project_messages(model_history.replay_history(dbrows)),u.project_messages(rows))

    def test_raw_state_and_archive_are_not_replaced_with_deltas(self):
        rows=[{'role':'user','content':'question'},*tool(state()),*tool(state(2,0.2),'status-2'),
              {'role':'assistant','content':'explanation'}]
        u.freeze_new_messages(rows,0)
        group=[{'id':1,'role':'user','content':'question'},
               {'id':2,'role':'assistant','content':'explanation',
                'metadata':{'model_turn':{'version':1,'messages':rows}}}]
        archive=mastery.archive_text(group)
        self.assertIn('Practice past tense',archive)
        self.assertNotIn(u.PROJECTION_KEY,archive)
        self.assertNotIn('base_id',archive)

    def test_mutation_after_freeze_fails_clearly(self):
        rows=[snapshot(state(brief=True))];u.freeze_new_messages(rows,0)
        rows[0]['content']=rows[0]['content'].replace('Chinese explanations','different explanations')
        with self.assertRaisesRegex(ValueError,'Frozen mastery state'):
            u.project_messages(rows)

    def test_mastery_gets_fresh_marker_even_if_state_is_identical(self):
        old=snapshot(state(brief=True))
        host=pipeline_module.AgenticLoopPipeline.__new__(pipeline_module.AgenticLoopPipeline)
        host.fresh_mastery_state_each_turn=True
        host._runtime_snapshots={'mastery_status_snapshot':old['content']}
        host._build_system_prompt=lambda *a,**kw:'stable system'
        host._prompt_assembler=NS(user_message=lambda **kw:'new question')
        host._prepare_messages_with_attachments=lambda messages,context:messages
        context=NS(runtime=NS(model_history=[old]),conversation_history=[],metadata={})
        rows=host._build_loop_messages(context=context,enabled_tools=[])
        self.assertEqual(rows[1],old)
        self.assertEqual(rows[-2]['content'],old['content'])
        u.freeze_new_messages(rows,host._model_turn_start)
        self.assertEqual(envelope(u.project_messages(rows)[-2])['format'],'unchanged')

    def test_sidecar_does_not_inflate_original_history_budget(self):
        rows=[snapshot(state(brief=True))]
        before=mastery._memory_tokens(rows,'')
        u.freeze_new_messages(rows,0)
        self.assertEqual(mastery._memory_tokens(rows,''),before)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StateTests))
    print(json.dumps({'state_tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)}),flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)
