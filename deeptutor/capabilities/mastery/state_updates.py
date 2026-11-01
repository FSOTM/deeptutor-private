"""Freeze only new state messages; keep originals for grading and exact recall.

An ordinary replay never changes a frozen model representation. When compaction
or a branch removes a delta's base, its archived full payload supplies a complete
baseline in that request. No missing state is inferred from a language summary.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

PROJECTION_KEY = '_mastery_state_projection'
STATE_UPDATE_NOTICE = (
    '\n\n[Verified mastery state updates]\n'
    'The runtime reads authoritative mastery state at the start of EVERY turn. '
    'A new mastery_status_snapshot containing mastery_state confirms that THIS turn '
    'has already checked it, even when format=unchanged; do not call mastery_status '
    'again just to begin. mastery_state format=full supplies data and its state_id. '
    'format=delta or unchanged names a previously supplied base_id and supplies '
    'JSON Pointer changes (add/replace/remove); unchanged fields retain their base '
    'values. The current object repeats the authoritative next objective, mode, '
    'revision and pending interaction so you need not calculate them. The newest '
    'verified state supersedes historical states. Actual grading and prerequisites '
    'are decided by the engine, never by reconstructing scores yourself. Call '
    'mastery_status when updated state is needed after a mutation, or to inspect '
    'the full outline; it preserves all fields through the same lossless updates. '
    'If a base is no longer present after compaction, the runtime supplies full data. '
    'Learner requests, answers and source evidence are not state deltas.'
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _raw_digest(content):
    return hashlib.sha256(content.encode()).hexdigest()


def changes(before, after, path=''):
    if type(before) is not type(after):
        return [{'op': 'replace', 'path': path, 'value': after}]
    if isinstance(before, dict):
        result = [{'op': 'remove', 'path': path + '/' + _escape(key)}
                  for key in sorted(before.keys() - after.keys())]
        for key in sorted(after):
            pointer = path + '/' + _escape(key)
            if key not in before:
                result.append({'op': 'add', 'path': pointer, 'value': after[key]})
            else:
                result.extend(changes(before[key], after[key], pointer))
        return result
    if isinstance(before, list) and len(before) == len(after):
        return [op for i, (a, b) in enumerate(zip(before, after))
                for op in changes(a, b, path + '/' + str(i))]
    return [] if before == after else [{'op': 'replace', 'path': path, 'value': after}]


def _escape(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def apply_changes(before, operations):
    result = deepcopy(before)
    for op in operations:
        if op['path'] == '':
            result = deepcopy(op['value'])
            continue
        keys = [p.replace('~1', '/').replace('~0', '~') for p in op['path'][1:].split('/')]
        owner = result
        for key in keys[:-1]:
            owner = owner[int(key)] if isinstance(owner, list) else owner[key]
        key = int(keys[-1]) if isinstance(owner, list) else keys[-1]
        if op['op'] == 'remove':
            del owner[key]
        else:
            owner[key] = deepcopy(op['value'])
    return result


def _state(message, names):
    content = message.get('content')
    if not isinstance(content, str):
        return None
    snapshot = message.get('_context_snapshot') == 'mastery_status_snapshot'
    tool = message.get('role') == 'tool' and (
        message.get('name') or names.get(message.get('tool_call_id'))) == 'mastery_status'
    if not snapshot and not tool:
        return None
    text = content
    if snapshot:
        from deeptutor.services.session.model_history import _MASTERY_SNAPSHOT_NOTICE
        if not text.startswith(_MASTERY_SNAPSHOT_NOTICE):
            return None
        text = text[len(_MASTERY_SNAPSHOT_NOTICE):]
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict) or not isinstance(payload.get('path_id'), str):
            return None
        canonical(payload)
    except (ValueError, TypeError):
        return None
    scope = 'working_brief' if payload.get('state_scope') == 'working_brief' else 'full'
    return payload, (payload['path_id'], payload.get('mode'), scope)


def _full(payload, key):
    return {'format': 'full', 'state_id': digest(payload), 'scope': key[2], 'data': payload}


def _content(message, envelope):
    rendered = canonical({'mastery_state': envelope})
    if message.get('_context_snapshot') == 'mastery_status_snapshot':
        return '[Current-turn mastery_status snapshot; freshly verified by the runtime]\n' + rendered
    return rendered


def _current(payload):
    # Keep operational decisions readable, not dependent on model arithmetic.
    return {key: deepcopy(payload.get(key)) for key in (
        'status', 'path_id', 'path_revision', 'mode', 'next',
        'pending_interaction', 'intake_needed')}


def freeze_new_messages(messages, start):
    """Attach a durable model representation only to previously unsent messages."""
    previous = {}
    names = {}
    for index, message in enumerate(messages):
        for call in message.get('tool_calls') or []:
            names[call['id']] = call.get('function', {}).get('name')
        parsed = _state(message, names)
        if parsed is None:
            continue
        payload, key = parsed
        frozen = message.get(PROJECTION_KEY)
        if frozen is not None:
            _validated(frozen, message['content'], payload, key)
        elif index >= start:
            envelope = _full(payload, key)
            prior = previous.get(key)
            if prior is not None:
                operations = changes(prior, payload)
                if canonical(apply_changes(prior, operations)) != canonical(payload):
                    raise ValueError('Mastery state update failed lossless reconstruction')
                delta = {'format': 'delta' if operations else 'unchanged',
                         'state_id': digest(payload), 'base_id': digest(prior),
                         'scope': key[2], 'changes': operations, 'current': _current(payload)}
                if len(canonical(delta)) < len(canonical(envelope)):
                    envelope = delta
            message[PROJECTION_KEY] = {
                'version': 1, 'raw_sha256': _raw_digest(message['content']),
                'state_id': digest(payload), 'scope': key[2],
                'base_id': envelope.get('base_id'), 'content': _content(message, envelope)}
        previous[key] = payload


def _validated(frozen, content, payload, key):
    if (not isinstance(frozen, dict) or frozen.get('version') != 1
            or frozen.get('raw_sha256') != _raw_digest(content)
            or frozen.get('state_id') != digest(payload) or frozen.get('scope') != key[2]
            or not isinstance(frozen.get('content'), str)):
        raise ValueError('Frozen mastery state changed or is invalid; refusing inconsistent replay')


def project_messages(messages):
    """Replay frozen bytes; recover a full baseline only if its base disappeared."""
    projected = []
    known = set()
    names = {}
    for message in messages:
        for call in message.get('tool_calls') or []:
            names[call['id']] = call.get('function', {}).get('name')
        copy = dict(message)
        parsed = _state(message, names)
        if parsed is not None:
            payload, key = parsed
            identity = (key, digest(payload))
            frozen = message.get(PROJECTION_KEY)
            if frozen is not None:
                _validated(frozen, message['content'], payload, key)
                base = frozen.get('base_id')
                if base and (key, base) not in known:
                    # Compaction/branching may remove an ancestor. Rehydrate
                    # from the exact archived payload, never from an LLM summary.
                    copy['content'] = _content(message, _full(payload, key))
                else:
                    copy['content'] = frozen['content']
            known.add(identity)
        copy.pop(PROJECTION_KEY, None)
        projected.append(copy)
    return projected
