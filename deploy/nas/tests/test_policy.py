"""No network calls: threshold, projection, archive and early-compaction regressions."""
import asyncio,copy,importlib.util,json,os,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).parent
sys.path.insert(0,'/app')
os.environ['DEEPTUTOR_AUTO_COMPACT_TOKENS']='500000'
def load(name, rel):
    # Exercise the actual candidate package baked into the image.
    return __import__(name, fromlist=['*'])
policy=load('deeptutor.services.session.total_compaction','services/session/total_compaction.py')
model_history=load('deeptutor.services.session.model_history','services/session/model_history.py')
history=load('deeptutor.capabilities.mastery.history','capabilities/mastery/history.py')
base=load('deeptutor.services.session.context_builder','services/session/context_builder.py')
mastery=load('deeptutor.capabilities.mastery.memory','capabilities/mastery/memory.py')
pipeline_module=load('deeptutor.agents.loop.pipeline','agents/loop/pipeline.py')
updates=load('deeptutor.capabilities.mastery.state_updates','capabilities/mastery/state_updates.py')
mastery_pipeline=load('deeptutor.capabilities.mastery.pipeline','capabilities/mastery/pipeline.py')
agent=load('deeptutor.agents.loop.agent_loop','agents/loop/agent_loop.py')

class Store:
    def __init__(self,rows=None):self.session={};self.rows=rows or [];self.writes=0
    async def get_session(self,*a):return dict(self.session)
    async def get_messages_for_context(self,*a,**kw):return copy.deepcopy(self.rows)
    async def update_summary(self,sid,summary,watermark):
        self.session.update(compressed_summary=summary,summary_up_to_msg_id=watermark);self.writes+=1

class Stream:
    def __init__(self):self.events=[]
    async def progress(self,*a,**kw):self.events.append((a,kw))
    async def content(self,*a,**kw):self.events.append((a,kw))
    async def thinking(self,*a,**kw):self.events.append((a,kw))

def make_loop(builder=None):
    extensions={'total_context_compaction':{'builder':builder,'leaf_message_id':77}}
    cfg=NS(model='gpt-6.1-sol',context_window=1000000,max_tokens=4096,binding='openai')
    pipeline=NS(llm_config=cfg,language='zh',_model_turn_start=2,
                _request_message_tokens=lambda m:len(m.get('content') or ''),
                _tool_schema_tokens=lambda t:sum(len(x.get('description','')) for x in t or []))
    context=NS(session_id='isolated-test',extension=lambda name:extensions.setdefault(name,{}),
               runtime=NS(model_history=None),metadata={})
    return NS(pipeline=pipeline,context=context,stream=Stream(),source='test',stage='answer')

class FakeBuilder:
    def __init__(self,success=True):self.store=Store();self.success=success;self.calls=0
    async def build(self,**kw):
        self.calls+=1
        self.forced_budget=self._total_context_forced_budget
        assert kw['leaf_message_id']==77
        if self.success:await self.store.update_summary('isolated-test','retained learning facts',99)
        return NS(conversation_summary='retained learning facts',model_history=[{'role':'user','content':'recent history'}],history_sources={})

class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_compacted_summary_is_saved_in_exact_sent_position(self):
        builder=FakeBuilder();loop=make_loop(builder)
        rows=[{'role':'system','content':'s'},{'role':'user','content':'h'*500000},
              {'role':'user','content':'old summary','_context_snapshot':'conversation_summary'},
              {'role':'user','content':'current learner words'}]
        original=copy.deepcopy(rows)
        sent=await policy.enforce_total_budget(loop,rows,[],lambda x:x)
        saved=policy.turn_messages_for_storage(loop,rows)
        self.assertEqual(saved[0]['_context_snapshot'],'conversation_summary')
        self.assertIn('retained learning facts',saved[0]['content'])
        self.assertNotIn('old summary',[m.get('content') for m in saved])
        # Next turn replays retained history followed by this saved turn.
        replayed=[rows[0],*loop._total_compacted_history,*saved]
        self.assertEqual(replayed,sent)
        self.assertEqual(rows,original)
        normalized=model_history.normalize_model_turn({'version':1,'messages':saved})
        self.assertEqual(normalized['messages'],saved)

    async def test_normal_turn_storage_preserves_original_bytes(self):
        loop=make_loop()
        rows=[{'role':'system','content':'s'},{'role':'user','content':'past'},
              {'role':'user','content':'new learner input'}]
        self.assertEqual(policy.turn_messages_for_storage(loop,rows),rows[2:])

    async def full_reply(self,compact=False,extra_body=None):
        host=make_loop(FakeBuilder() if compact else None)
        host.pipeline.model='gpt-6.1-sol';host.pipeline.binding='openai';host.pipeline.usage=None
        host.pipeline._completion_kwargs=lambda **kw:({'extra_body':extra_body} if extra_body is not None else {})
        async def guard(*a,**kw):pass
        host.pipeline._guard_context_window=guard
        loop=agent.AgentLoop.__new__(agent.AgentLoop);loop.__dict__.update(host.__dict__)
        loop._transient_model_messages=[];loop._request_fingerprint=None;loop._last_request=None
        loop._request_tools=[];loop._tool_schema_catalog=[];loop._apply_prompt_caching=lambda kwargs:None
        async def response_stream(*a):
            expected=(extra_body or {}).get('prompt_cache_key') or policy.session_cache_key(loop.pipeline.llm_config,loop.context.session_id)
            self.assertEqual(a[0]['extra_body']['prompt_cache_key'],expected)
            if extra_body is not None:self.assertEqual({k:v for k,v in a[0]['extra_body'].items() if k!='prompt_cache_key'},{k:v for k,v in extra_body.items() if k!='prompt_cache_key'})
            async def chunks():
                yield NS(usage=None,choices=[NS(finish_reason='stop',delta=NS(content='verification passed'))])
            return chunks()
        loop._create_response_stream=response_stream
        rows=[{'role':'system','content':'s'},{'role':'user','content':'h'*(500000 if compact else 100)},{'role':'user','content':'u'}]
        result=await loop._call_llm(messages=rows,label='test',call_kind='test',trace_role='test',max_tokens=100)
        self.assertEqual(result.text,'verification passed')
        self.assertLess(policy.request_tokens(loop.pipeline,loop._last_request.messages,[]),500000)
        self.assertEqual(len(rows[1]['content']),500000 if compact else 100)

    async def test_full_reply_without_compaction(self):await self.full_reply(False)
    async def test_full_reply_after_compaction(self):await self.full_reply(True)

    async def test_explicit_session_cache_key_is_preserved(self):
        original={'prompt_cache_key':'existing-client-key','other':'keep'}
        await self.full_reply(False,original)
        self.assertEqual(original,{'prompt_cache_key':'existing-client-key','other':'keep'})

    async def test_499999_does_not_compact(self):
        builder=FakeBuilder();loop=make_loop(builder)
        rows=[{'role':'system','content':'s'*20000},{'role':'user','content':'h'*479978},{'role':'user','content':'u'}]
        result=await policy.enforce_total_budget(loop,rows,[{'description':'t'*20}],lambda x:x)
        self.assertEqual(policy.request_tokens(loop.pipeline,result,[{'description':'t'*20}]),499999)
        self.assertEqual(builder.calls,0)

    async def test_exact_500000_counts_tools_and_preserves_archive(self):
        builder=FakeBuilder();loop=make_loop(builder)
        rows=[{'role':'system','content':'s'*20000},{'role':'user','content':'h'*479979},{'role':'user','content':'u'}]
        original=copy.deepcopy(rows);tools=[{'description':'t'*20}]
        result=await policy.enforce_total_budget(loop,rows,tools,lambda x:x)
        self.assertEqual(builder.calls,1);self.assertEqual(builder.store.writes,1)
        self.assertEqual(builder.forced_budget,20000)
        self.assertLess(policy.request_tokens(loop.pipeline,result,tools),500000)
        self.assertEqual(rows,original);self.assertEqual(result[-1]['content'],'u')
        self.assertFalse(hasattr(builder,'_total_context_forced_budget'))
        await policy.enforce_total_budget(loop,rows,tools,lambda x:x)
        self.assertEqual(builder.calls,1)

    async def test_transient_retrieval_counts_toward_trigger(self):
        builder=FakeBuilder();loop=make_loop(builder)
        rows=[{'role':'system','content':'s'},{'role':'user','content':'h'*480000},{'role':'user','content':'u'}]
        extra={'role':'user','content':'retrieved'*3000}
        result=await policy.enforce_total_budget(loop,rows,[],lambda x:[*x,extra])
        self.assertEqual(builder.calls,1);self.assertEqual(result[-1],extra)

    async def test_current_tool_summary_preserves_protocol_and_raw_result(self):
        loop=make_loop();loop.pipeline._model_turn_start=1
        rows=[{'role':'system','content':'s'},{'role':'user','content':'latest question'},
              {'role':'assistant','content':'','tool_calls':[{'id':'call-1','type':'function','function':{'name':'rag','arguments':'{}'}}]},
              {'role':'tool','tool_call_id':'call-1','content':'e'*500000}]
        original=copy.deepcopy(rows);calls=[];old=policy.summarize_tool
        async def summarize(*a):calls.append(1);return 'evidence summary with source anchors'
        policy.summarize_tool=summarize
        try:
            result=await policy.enforce_total_budget(loop,rows,[],lambda x:x)
            self.assertEqual(rows,original);self.assertEqual(result[-1]['tool_call_id'],'call-1')
            await policy.enforce_total_budget(loop,rows,[],lambda x:x)
            self.assertEqual(len(calls),1)
        finally:policy.summarize_tool=old

    async def test_uncompressible_input_fails_without_dropping_user_text(self):
        loop=make_loop();rows=[{'role':'system','content':'s'},{'role':'user','content':'u'*500000}]
        with self.assertRaisesRegex(RuntimeError,'未发送'):await policy.enforce_total_budget(loop,rows,[],lambda x:x)
        self.assertEqual(len(rows[-1]['content']),500000)

    async def test_failed_summary_does_not_use_destructive_fallback(self):
        builder=FakeBuilder(success=False);loop=make_loop(builder)
        rows=[{'role':'system','content':'s'},{'role':'user','content':'h'*500000},{'role':'user','content':'u'}]
        with self.assertRaises(RuntimeError):await policy.enforce_total_budget(loop,rows,[],lambda x:x)
        self.assertEqual(builder.store.writes,0)
        self.assertFalse(hasattr(loop,'_total_compacted_history'))

    async def test_mastery_above_20000_retains_full_history(self):
        rows=[{'id':i,'role':'user' if i%2 else 'assistant','content':' learning'*4000} for i in range(1,9)]
        store=Store(rows);builder=mastery.MasteryContextBuilder(store);cfg=make_loop().pipeline.llm_config
        async def unexpected(**kw):raise AssertionError('premature compression')
        builder._summarize=unexpected
        result=await builder.build(session_id='isolated-test',llm_config=cfg)
        self.assertGreater(result.token_count,20000);self.assertEqual(store.writes,0)
        self.assertEqual(len(result.model_history),len(rows));self.assertEqual(store.rows,rows)

    async def test_general_above_131072_retains_history(self):
        rows=[{'id':i,'role':'user','content':' learning'*35000} for i in range(1,5)]
        store=Store(rows);builder=base.ContextBuilder(store);cfg=make_loop().pipeline.llm_config
        async def unexpected(**kw):raise AssertionError('premature compression')
        builder._summarize=unexpected
        result=await builder.build(session_id='isolated-test',llm_config=cfg)
        self.assertGreater(result.token_count,131072);self.assertEqual(store.writes,0)
        self.assertEqual(len(result.model_history),len(rows))

    async def test_unselected_models_keep_original_policy(self):
        cfg=NS(model='gpt-4o',context_window=128000,max_tokens=4096)
        self.assertIsNone(policy.total_limit(cfg))
        self.assertEqual(mastery.MasteryContextBuilder(None)._history_budget(cfg),20000)

    async def test_post_compaction_uses_upstream_history_split(self):
        builder=mastery.MasteryContextBuilder(None);cfg=make_loop().pipeline.llm_config
        builder._total_context_forced_budget=20000
        self.assertEqual(builder._history_budget(cfg),20000)
        self.assertEqual(builder._recent_budget(20000),12000)
        self.assertEqual(builder._summary_budget(20000,cfg),4096)

    async def test_session_cache_identity_is_stable_isolated_and_scoped(self):
        cfg=make_loop().pipeline.llm_config
        self.assertEqual(policy.session_cache_key(cfg,'session-a'),policy.session_cache_key(cfg,'session-a'))
        self.assertNotEqual(policy.session_cache_key(cfg,'session-a'),policy.session_cache_key(cfg,'session-b'))
        self.assertNotIn('session-a',policy.session_cache_key(cfg,'session-a'))
        self.assertIsNone(policy.session_cache_key(cfg,'direct'))
        self.assertIsNone(policy.session_cache_key(cfg,''))
        cfg.binding='anthropic'
        self.assertIsNone(policy.session_cache_key(cfg,'session-a'))

    async def test_replay_preserves_old_snapshots_and_recalled_bodies(self):
        messages=[{'role':'user','content':'old status','_context_snapshot':'runtime_context'},
            {'role':'assistant','content':'','tool_calls':[{'id':'read-1','function':{'name':'read_source','arguments':'{"source_id":"hs-tutor-4"}'}}]},
            {'role':'tool','tool_call_id':'read-1','content':'full original recall body'},
            {'role':'user','content':'new status','_context_snapshot':'runtime_context'}]
        original=copy.deepcopy(messages)
        self.assertEqual(history.project_mastery_replay(messages),original)
        self.assertEqual(messages,original)
        from unittest.mock import patch
        with patch.object(model_history,'history_groups',return_value=[[{}]]),patch.object(model_history,'replay_group',return_value=copy.deepcopy(messages)):
            self.assertEqual(model_history.replay_history([{}]),original)

    async def test_marked_mastery_snapshot_survives_legacy_input_cleanup(self):
        snapshot={'role':'user','content':model_history._MASTERY_SNAPSHOT_NOTICE+json.dumps({'path_id':'test-topic','map':{'new':1}}),'_context_snapshot':'mastery_status_snapshot'}
        messages=[snapshot,{'role':'user','content':'question'},{'role':'assistant','content':'answer'}]
        rows=[{'id':1,'role':'user','content':'question'},
              {'id':2,'role':'assistant','content':'answer','metadata':{'model_turn':{'version':1,'messages':copy.deepcopy(messages)}}}]
        self.assertEqual(model_history.replay_history(rows),messages)
        self.assertEqual(rows[1]['metadata']['model_turn']['messages'],messages)

    async def test_legacy_fused_snapshot_cleanup_remains_supported(self):
        fused='question\n'+model_history._MASTERY_SNAPSHOT_NOTICE+json.dumps({'path_id':'old-topic'})
        rows=[{'id':1,'role':'user','content':'question'},
              {'id':2,'role':'assistant','content':'answer','metadata':{'model_turn':{'version':1,'messages':[{'role':'user','content':fused},{'role':'assistant','content':'answer'}]}}}]
        self.assertEqual(model_history.replay_history(rows)[0]['content'],'question')

    async def test_changed_runtime_state_is_appended_not_replaced(self):
        previous=[{'role':'user','content':'old status','_context_snapshot':'runtime_context'},
                  {'role':'user','content':'question 1'},{'role':'assistant','content':'answer 1'}]
        host=pipeline_module.AgenticLoopPipeline.__new__(pipeline_module.AgenticLoopPipeline)
        host._runtime_snapshots={'runtime_context':'new status'}
        host._build_system_prompt=lambda *a,**kw:'stable system'
        host._prompt_assembler=NS(user_message=lambda **kw:'question 2')
        host._prepare_messages_with_attachments=lambda messages,context:messages
        context=NS(runtime=NS(model_history=previous),conversation_history=[],metadata={})
        result=host._build_loop_messages(context=context,enabled_tools=[])
        self.assertEqual(result[1:1+len(previous)],previous)
        self.assertEqual(result[-2]['content'],'new status')
        self.assertEqual(result[-1]['content'],'question 2')
        self.assertEqual(host._model_turn_start,len(previous)+1)

    async def test_unchanged_runtime_state_is_not_duplicated(self):
        previous=[{'role':'user','content':'same status','_context_snapshot':'runtime_context'}]
        host=pipeline_module.AgenticLoopPipeline.__new__(pipeline_module.AgenticLoopPipeline)
        host._runtime_snapshots={'runtime_context':'same status'}
        host._build_system_prompt=lambda *a,**kw:'stable system'
        host._prompt_assembler=NS(user_message=lambda **kw:'next question')
        host._prepare_messages_with_attachments=lambda messages,context:messages
        context=NS(runtime=NS(model_history=previous),conversation_history=[],metadata={})
        result=host._build_loop_messages(context=context,enabled_tools=[])
        self.assertEqual(result,[{'role':'system','content':'stable system'},*previous,{'role':'user','content':'next question'}])

    async def test_wired_before_actual_provider_guard(self):
        host=make_loop(FakeBuilder())
        async def guard(projected,*a,**kw):
            self.assertLess(policy.request_tokens(host.pipeline,projected,kw.get('tool_schemas')),500000)
            raise LookupError('provider boundary reached')
        host.pipeline._guard_context_window=guard
        loop=agent.AgentLoop.__new__(agent.AgentLoop)
        loop.__dict__.update(host.__dict__);loop._transient_model_messages=[]
        rows=[{'role':'system','content':'s'},{'role':'user','content':'h'*500000},{'role':'user','content':'u'}]
        with self.assertRaisesRegex(LookupError,'provider boundary'):
            await loop._call_llm(messages=rows,label='test',call_kind='test',trace_role='test',max_tokens=100)
        self.assertEqual(loop.context.extension('total_context_compaction')['builder'].calls,1)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PolicyTests))
    print(json.dumps({'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors)}),flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)
