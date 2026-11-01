"""Private data-preservation checks. Reports contain hashes, never learner text."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def capture(data):
    result={'settings':{},'messages':{},'learning':{}}
    settings=data/'user/settings'
    for p in settings.glob('*.json'):
        value=json.loads(p.read_text(encoding='utf-8'))
        if isinstance(value,dict):result['settings'][p.name]={k:fingerprint(v) for k,v in value.items()}
    for p in (data/'user').rglob('chat_history.db'):
        if p.is_symlink():raise RuntimeError('Unexpected database symlink')
        db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        rows={}
        for row in db.execute('SELECT id,session_id,role,content,capability,events_json,attachments_json,metadata_json,created_at,parent_message_id FROM messages'):
            value=dict(row);metadata=json.loads(value.pop('metadata_json') or '{}')
            rows[str(value['id'])]={'content':fingerprint(value),'metadata':{k:fingerprint(v) for k,v in metadata.items()}}
        result['messages'][p.relative_to(data).as_posix()]=rows
        db.close()
    from deeptutor.learning.storage import LearningStore
    for p in (data/'user').rglob('mastery.sqlite3'):
        store=LearningStore(root=p.parent)
        paths={}
        for pid in store.list_all():
            progress=store.load(pid)
            if progress is None:raise RuntimeError('Learning path could not load')
            paths[pid]={'progress':{k:fingerprint(v) for k,v in progress.model_dump(mode='json').items()},
                        'interactions':{i.interaction_id:fingerprint(i.model_dump(mode='json')) for i in store.list_interactions(pid,limit=10000)}}
        result['learning'][p.relative_to(data).as_posix()]=paths
    return result


def assert_preserved(before,after,path='data'):
    if isinstance(before,dict):
        if not isinstance(after,dict):raise RuntimeError('Data shape changed at '+path)
        for key,value in before.items():
            if key not in after:raise RuntimeError('Preserved record missing at '+path+'/'+key)
            assert_preserved(value,after[key],path+'/'+key)
    elif before!=after:
        raise RuntimeError('Preserved value changed at '+path)


def verify_policy(data):
    from deeptutor.services.llm.config import get_llm_config
    from deeptutor.services.session.total_compaction import total_limit,RETAINED_HISTORY_TOKENS,session_cache_key
    from deeptutor.capabilities.mastery.memory import MasteryContextBuilder
    config=get_llm_config()
    if config.context_window!=1000000 or total_limit(config)!=500000:raise RuntimeError('Window/compaction policy changed')
    builder=MasteryContextBuilder(None)
    if builder._history_budget(config)!=1000000:raise RuntimeError('Premature history compaction restored')
    builder._total_context_forced_budget=20000
    if builder._history_budget(config)!=20000 or RETAINED_HISTORY_TOKENS!=20000:raise RuntimeError('Retained history budget changed')
    if not session_cache_key(config,'nas-verification-session'):raise RuntimeError('Session cache identity missing')
    catalog=json.loads((data/'user/settings/model_catalog.json').read_text())
    seen=set()
    def visit(value):
        if isinstance(value,dict):
            if value.get('model') in ['gpt-6.1-sol','gpt-6-astra'] and 'context_window' in value:
                if value['context_window']!=1000000:raise RuntimeError('Model catalog capacity changed')
                seen.add(value['model'])
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(catalog)
    if seen!={'gpt-6.1-sol','gpt-6-astra'}:raise RuntimeError('Configured model capacity entries missing')
    return {'window':1000000,'trigger':500000,'retained_history':20000,'cache_identity_present':True}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=Path('/app/data'))
    parser.add_argument('--capture',type=Path)
    parser.add_argument('--check',type=Path)
    args=parser.parse_args()
    if args.capture:
        value=capture(args.data)
        args.capture.write_text(json.dumps(value),encoding='utf-8')
        print(json.dumps({'captured_chat_databases':len(value['messages']),'captured_learning_databases':len(value['learning'])}))
    if args.check:
        before=json.loads(args.check.read_text(encoding='utf-8'))
        assert_preserved(before,capture(args.data))
        print(json.dumps({'existing_learning_and_messages_preserved':True,'policy':verify_policy(args.data)}))


if __name__=='__main__':main()
