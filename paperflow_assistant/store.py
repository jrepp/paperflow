"""Private durable queue, indexed evidence, memory, and validation receipts."""
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from decimal import Decimal
from uuid import uuid4

from paperflow_storage import ConflictError
from paperflow_assistant.quality import wilson


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class AssistantStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self, write=False):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connection(True) as conn:
            for sql in SCHEMA.split(';'):
                if sql.strip():
                    conn.execute(sql)

    def recover(self):
        # The monitor's process lock establishes exclusive ownership before this call.
        with self.connection(True) as conn:
            conn.execute("UPDATE assistant_jobs SET status='queued', error='Interrupted', available_at=? WHERE status='running'", (time.time(),))
            conn.execute("UPDATE assistant_attempts SET status='interrupted',finished_at=? WHERE status='running'", (time.time(),))
            conn.execute("UPDATE assistant_requests SET payload=? WHERE payload IS NULL",
                         (encode({'status': 'interrupted', 'usage': None, 'estimated_usd': None}),))

    def reconcile(self, storage, fingerprint, batch=200):
        with self.connection() as conn:
            cursor = conn.execute("SELECT value FROM assistant_meta WHERE key='source_cursor'").fetchone()
            cursor = int(cursor[0]) if cursor else 0
        source = storage._connect()
        try:
            rows = source.execute("""SELECT r.rowid,r.payload,i.payload FROM research_revisions r
                JOIN research_items i ON i.id=r.item_id WHERE r.rowid>? ORDER BY r.rowid LIMIT ?""", (cursor, batch)).fetchall()
        finally:
            source.close()
        with self.connection(True) as conn:
            for rowid, revision_json, item_json in rows:
                rev, item = json.loads(revision_json), json.loads(item_json)
                conn.execute("INSERT OR IGNORE INTO assistant_documents VALUES (?,?,?,?,?,?)",
                             (rev['id'], rev['item_id'], item['title'], item['kind'], revision_json, item_json))
                # Chunk offsets remain exact Unicode positions in the immutable Markdown.
                for start in range(0, len(rev['markdown']), 3000):
                    conn.execute("INSERT INTO assistant_history VALUES (?,?,?,?,?)",
                                 (rev['id'], item['title'], start, rev['markdown'][start:start+3000], rev['created_at']))
                cursor = rowid
            conn.execute("INSERT OR REPLACE INTO assistant_meta VALUES ('source_cursor',?)", (str(cursor),))
            docs = conn.execute("SELECT revision_id FROM assistant_documents WHERE kind IN ('paper','article','discussion')").fetchall()
            for doc in docs:
                key = hashlib.sha256((fingerprint + doc[0]).encode()).hexdigest()
                conn.execute("""INSERT OR IGNORE INTO assistant_jobs
                    (id,revision_id,fingerprint,status,queued_at,available_at) VALUES (?,?,?,'queued',?,?)""",
                             (key, doc[0], fingerprint, time.time(), time.time()))
        return len(rows)

    def claim(self, max_attempts, daily_attempt_limit=100):
        with self.connection(True) as conn:
            today = datetime.now(UTC).date().isoformat()
            daily = conn.execute("SELECT value FROM assistant_meta WHERE key=?", ('attempts:'+today,)).fetchone()
            count = int(daily[0]) if daily else 0
            if count >= daily_attempt_limit:
                return None
            conn.execute("UPDATE assistant_jobs SET status='failed',error='AttemptsExhausted' WHERE status='queued' AND attempts>=?", (max_attempts,))
            row = conn.execute("""SELECT j.* FROM assistant_jobs j
                JOIN assistant_documents d ON d.revision_id=j.revision_id
                WHERE j.status='queued' AND j.available_at<=?
                ORDER BY json_extract(d.revision,'$.created_at') DESC,j.queued_at,j.id LIMIT 1""", (time.time(),)).fetchone()
            if row is None:
                return None
            conn.execute("INSERT OR REPLACE INTO assistant_meta VALUES (?,?)", ('attempts:'+today, str(count+1)))
            conn.execute("UPDATE assistant_jobs SET status='running',attempts=attempts+1,started_at=? WHERE id=?", (time.time(), row['id']))
            conn.execute("INSERT INTO assistant_attempts (job_id,attempt,status,started_at) VALUES (?,?,'running',?)",
                         (row['id'], row['attempts']+1, time.time()))
            return dict(conn.execute("SELECT * FROM assistant_jobs WHERE id=?", (row['id'],)).fetchone())

    def document(self, revision_id):
        with self.connection() as conn:
            row = conn.execute("SELECT revision,item FROM assistant_documents WHERE revision_id=?", (str(revision_id),)).fetchone()
            if row is None:
                raise ValueError("Revision is not indexed")
            return json.loads(row[0]), json.loads(row[1])

    def finish(self, job_id, analysis, validation, evaluation, usage, duration):
        with self.connection(True) as conn:
            conn.execute("""UPDATE assistant_jobs SET status='succeeded',finished_at=?,output=?,validation=?,evaluation=?,usage=?,duration=?,error=NULL
                WHERE id=? AND status='running'""", (time.time(), encode(analysis), encode(validation), encode(evaluation), encode(usage), duration, job_id))
            conn.execute("UPDATE assistant_attempts SET status='succeeded',finished_at=?,usage=? WHERE job_id=? AND status='running'",
                         (time.time(), encode(usage), job_id))

    def fail(self, job, error, settings, duration, usage=None):
        with self.connection(True) as conn:
            conn.execute("""UPDATE assistant_jobs SET status=?,error=?,finished_at=?,duration=?,available_at=? WHERE id=?""",
                         ('queued' if job['attempts'] < settings.max_attempts else 'failed',
                          error, time.time(), duration, time.time()+settings.retry_seconds * job['attempts'], job['id']))
            conn.execute("UPDATE assistant_attempts SET status='failed',finished_at=?,usage=?,error=? WHERE job_id=? AND status='running'",
                         (time.time(), encode(usage or {}), error, job['id']))

    def search_history(self, query, limit=8, after=None, before=None):
        tokens = re.findall(r'\w+', query)[:20]
        if not tokens:
            return []
        expression = ' OR '.join('"'+t+'"' for t in tokens)
        with self.connection() as conn:
            rows = conn.execute("""SELECT revision_id,title,start,text,created_at FROM assistant_history
                WHERE assistant_history MATCH ? AND (? IS NULL OR created_at>=?)
                AND (? IS NULL OR created_at<?) ORDER BY rank LIMIT ?""",
                                (expression, after, after, before, before, min(max(limit, 1), 20))).fetchall()
        return [dict(r) for r in rows]

    def memory_write(self, job_id, operation_key, data, model, *, record_id=None, expected_version=None, status=None):
        payload = {"data": data, "record_id": record_id, "expected_version": expected_version, "status": status}
        signature = hashlib.sha256(encode(payload).encode()).hexdigest()
        key = f'{job_id}:{operation_key}'
        with self.connection(True) as conn:
            prior = conn.execute("SELECT fingerprint,result FROM assistant_receipts WHERE key=?", (key,)).fetchone()
            if prior:
                if prior[0] != signature:
                    raise ConflictError("Memory key reused for a different operation")
                return json.loads(prior[1])
            if record_id:
                previous = conn.execute("SELECT * FROM assistant_memory WHERE id=? ORDER BY version DESC LIMIT 1", (record_id,)).fetchone()
                if not previous or previous['version'] != expected_version:
                    raise ConflictError("Memory changed or does not exist")
                if previous['kind'] != data['kind']:
                    raise ValueError("Memory kind cannot change")
                version = expected_version + 1
            else:
                record_id, version = str(uuid4()), 1
            allowed = {'open', 'resolved', 'dismissed'} if data['kind'] == 'follow_up' else {'provisional', 'supported', 'disputed', 'superseded'}
            status = status or ('open' if data['kind'] == 'follow_up' else 'provisional')
            if status not in allowed:
                raise ValueError("Invalid memory status")
            result = {"id": record_id, "version": version, "status": status, "data": data,
                      "processing_run_id": job_id, "model": model, "created_at": datetime.now(UTC).isoformat()}
            conn.execute("INSERT INTO assistant_memory VALUES (?,?,?,?,?)", (record_id, version, data['kind'], status, encode(result)))
            conn.execute("DELETE FROM assistant_memory_index WHERE id=?", (record_id,))
            conn.execute("INSERT INTO assistant_memory_index VALUES (?,?)", (record_id, encode(data)))
            conn.execute("INSERT INTO assistant_receipts VALUES (?,?,?)", (key, signature, encode(result)))
            return result

    def search_memory(self, query='', kind=None, limit=10, include_closed=False):
        tokens = re.findall(r'\w+', query)[:20]
        with self.connection() as conn:
            conditions = ["m.version=(SELECT MAX(v.version) FROM assistant_memory v WHERE v.id=m.id)"]
            params = []
            if tokens:
                conditions.append("m.id IN (SELECT id FROM assistant_memory_index WHERE assistant_memory_index MATCH ?)")
                params.append(' OR '.join('"'+t+'"' for t in tokens))
            if kind:
                conditions.append('m.kind=?')
                params.append(kind)
            if not include_closed:
                conditions.append("m.status NOT IN ('resolved','dismissed','superseded')")
            rows = conn.execute('SELECT payload FROM assistant_memory m WHERE '+' AND '.join(conditions)+' ORDER BY m.rowid DESC LIMIT ?', (*params, min(max(limit, 1), 20)))
            return [json.loads(r[0]) for r in rows]

    def record_decision(self, job_id, decision_id, payload):
        with self.connection(True) as conn:
            conn.execute("INSERT INTO assistant_decisions VALUES (?,?,?)", (job_id, decision_id, encode(payload)))

    def record_tool(self, job_id, name, outcome):
        with self.connection(True) as conn:
            conn.execute('INSERT INTO assistant_tool_events VALUES (?,?,?,?)', (job_id, name, outcome, time.time()))

    def start_request(self, request_id, job_id, attempt, role, model, at):
        with self.connection(True) as conn:
            conn.execute('INSERT INTO assistant_requests VALUES (?,?,?,?,?,?,NULL)',
                         (request_id, job_id, attempt, role, model, at))

    def finish_request(self, request_id, payload):
        with self.connection(True) as conn:
            conn.execute('UPDATE assistant_requests SET payload=? WHERE id=?', (encode(payload), request_id))

    def request_metrics(self, job_id, attempt):
        with self.connection() as conn:
            rows = conn.execute('SELECT role,model,payload FROM assistant_requests WHERE job_id=? AND attempt=?',
                                (job_id, attempt)).fetchall()
        return [{'role': r['role'], 'model': r['model'], **(json.loads(r['payload']) if r['payload'] else {})} for r in rows]

    def spend(self, days=7, planned_items_per_day=None):
        since = time.time()-days*86400
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM assistant_requests WHERE started_at>=? ORDER BY started_at', (since,)).fetchall()
            completed = conn.execute("SELECT COUNT(*) FROM assistant_jobs WHERE status='succeeded' AND finished_at>=?", (since,)).fetchone()[0]
        groups, daily = {}, {}
        total, retry_cost = Decimal(0), Decimal(0)
        missing, pending, priced = 0, 0, 0
        input_tokens, output_tokens, cache_tokens = 0, 0, 0
        for row in rows:
            payload = json.loads(row['payload']) if row['payload'] else {'status': 'pending'}
            key = row['role'] + ':' + (payload.get('returned_model') or row['model'])
            group = groups.setdefault(key, {'requests': 0, 'input_tokens': 0, 'output_tokens': 0,
                'cache_read_tokens': 0, 'estimated_usd': Decimal(0), 'unpriced_requests': 0})
            group['requests'] += 1
            day = datetime.fromtimestamp(row['started_at'], UTC).date().isoformat()
            day_total = daily.setdefault(day, {'estimated_usd': Decimal(0), 'requests': 0, 'unpriced_requests': 0})
            day_total['requests'] += 1
            usage = payload.get('usage') or {}
            for key in ('input_tokens', 'output_tokens', 'cache_read_tokens'):
                group[key] += usage.get(key, 0)
            input_tokens += usage.get('input_tokens', 0)
            output_tokens += usage.get('output_tokens', 0)
            cache_tokens += usage.get('cache_read_tokens', 0)
            cost = payload.get('estimated_usd')
            if cost is None:
                missing += 1
                pending += int(payload['status'] == 'pending')
                group['unpriced_requests'] += 1
                day_total['unpriced_requests'] += 1
            else:
                amount = Decimal(cost)
                priced += 1
                total += amount
                group['estimated_usd'] += amount
                day_total['estimated_usd'] += amount
                if row['attempt'] > 1:
                    retry_cost += amount
        for value in [*groups.values(), *daily.values()]:
            value['estimated_usd'] = str(value['estimated_usd'])
        average = total/completed if completed else None
        return {'window_days': days, 'currency': 'USD', 'estimated_usd': str(total),
                'billed_usd': None, 'priced_requests': priced, 'unpriced_requests': missing,
                'pending_requests': pending, 'input_tokens': input_tokens, 'output_tokens': output_tokens,
                'cache_read_tokens': cache_tokens, 'retry_estimated_usd': str(retry_cost),
                'completed_jobs': completed,
                'estimated_usd_per_completed_job': str(average) if average is not None else None,
                'tokens_per_completed_job': (input_tokens+output_tokens)/completed if completed else None,
                'by_role_and_model': groups, 'by_utc_day': daily,
                'planned_items_per_day': planned_items_per_day,
                'projected_daily_usd': str(average*planned_items_per_day) if average is not None and planned_items_per_day is not None else None,
                'estimate_incomplete': bool(missing),
                'limitations': 'Token-price estimate, not billed spend. Missing usage is unknown, not free. Projection assumes the observed work mix and retry rate. Costs and completions can cross window boundaries. Chinese public holidays require configured dates.'}

    def analysis(self, item_id):
        with self.connection() as conn:
            row = conn.execute("""SELECT j.id,j.revision_id,j.output,j.validation,j.evaluation FROM assistant_jobs j
                JOIN assistant_documents d ON d.revision_id=j.revision_id
                WHERE d.item_id=? AND j.status='succeeded' ORDER BY j.finished_at DESC LIMIT 1""", (str(item_id),)).fetchone()
        if not row:
            return None
        # Explicit public projection. Never return arbitrary records from the private store.
        from paperflow_assistant.models import Analysis
        return {'run_id': row['id'], 'revision_id': row['revision_id'],
                'analysis': Analysis.model_validate_json(row['output']).model_dump(mode='json'),
                'validation': json.loads(row['validation']), 'evaluation': json.loads(row['evaluation'])}

    def runs(self, limit=20, include_content=False):
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM assistant_jobs ORDER BY queued_at DESC,id LIMIT ?', (min(max(limit, 1), 100),)).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            for key in ('output', 'validation', 'evaluation', 'usage'):
                value[key] = json.loads(value[key]) if value[key] else None
            if not include_content:
                value.pop('output')
            result.append(value)
        return result

    def metrics(self):
        with self.connection() as conn:
            counts = dict(conn.execute('SELECT status,COUNT(*) FROM assistant_jobs GROUP BY status').fetchall())
            total_items = conn.execute('SELECT COUNT(DISTINCT item_id) FROM assistant_documents').fetchone()[0]
            eligible = conn.execute('SELECT COUNT(*) FROM assistant_jobs').fetchone()[0]
            retries = conn.execute('SELECT COALESCE(SUM(MAX(attempts-1,0)),0) FROM assistant_jobs').fetchone()[0]
            backlog = conn.execute("SELECT MIN(queued_at) FROM assistant_jobs WHERE status='queued'").fetchone()[0]
            rows = conn.execute("SELECT validation,evaluation,usage,duration FROM assistant_jobs WHERE status='succeeded'").fetchall()
            memories = conn.execute("""SELECT kind,COUNT(*) FROM assistant_memory m WHERE version=(SELECT MAX(version) FROM assistant_memory v WHERE v.id=m.id)
                AND status NOT IN ('resolved','dismissed','superseded') GROUP BY kind""").fetchall()
            attempt_counts = dict(conn.execute('SELECT status,COUNT(*) FROM assistant_attempts GROUP BY status').fetchall())
            attempts = conn.execute('SELECT usage FROM assistant_attempts WHERE usage IS NOT NULL').fetchall()
        quality, bases, citations_count, long_sentences, questions = {}, {}, 0, 0, 0
        usage_totals = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "tool_calls": 0}
        sampled, evaluated, errors, duration = 0, 0, 0, 0
        for validation, evaluation, usage, seconds in rows:
            v, e, u = json.loads(validation), json.loads(evaluation), json.loads(usage)
            bases[v['evidence_basis']] = bases.get(v['evidence_basis'], 0)+1
            citations_count += v['valid_citations']
            long_sentences += v['long_sentences']
            questions += v['questions']
            duration += seconds or 0
            for key in usage_totals:
                usage_totals[key] += u.get(key, 0)
            if e.get('selected'):
                sampled += 1
                if e.get('error'):
                    errors += 1
                elif e.get('judgment'):
                    evaluated += 1
                    group = e['evaluator_version'] + ':' + e['model']
                    entry = quality.setdefault(group, {"n": 0, "passed": 0, "dimensions": {}})
                    entry['n'] += 1
                    entry['passed'] += int(all(e['judgment'].values()))
                    for key, passed in e['judgment'].items():
                        entry['dimensions'][key] = entry['dimensions'].get(key, 0) + int(passed)
        for entry in quality.values():
            entry['pass_rate'] = entry['passed']/entry['n']
            entry['pass_rate_interval_95'] = wilson(entry['passed'], entry['n'])
        all_usage = {key: sum(json.loads(row[0]).get(key, 0) for row in attempts) for key in usage_totals}
        return {"counts": counts, "attempt_counts": attempt_counts, "usage_all_attempts": all_usage,
                "usage_limitations": "Provider-reported usage only. Interrupted or failed requests can incur unreported usage.",
                "job_unit": "source revision and harness configuration",
                "eligible_jobs": eligible, "indexed_items": total_items, "retries": retries,
                "completion_rate": counts.get('succeeded', 0)/eligible if eligible else None,
                "oldest_queue_age_seconds": time.time()-backlog if backlog else None,
                "evidence_basis": bases, "valid_quote_citations": citations_count,
                "long_sentences": long_sentences, "questions": questions,
                "active_memory": dict(memories), "usage_successful_runs": usage_totals,
                "duration_successful_runs_seconds": duration,
                "quality": {"kind": "model_estimate", "sample_selected": sampled,
                            "sample_evaluated": evaluated, "evaluation_errors": errors,
                            "groups": quality, "human_accuracy": None,
                            "limitations": "Sampled model judgments, not measured truth. Intervals describe evaluator pass rates. No human calibration yet."}}


SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assistant_documents (revision_id TEXT PRIMARY KEY,item_id TEXT,title TEXT,kind TEXT,revision TEXT,item TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS assistant_history USING fts5(revision_id UNINDEXED,title,start UNINDEXED,text,created_at UNINDEXED);
CREATE TABLE IF NOT EXISTS assistant_jobs (
 id TEXT PRIMARY KEY,revision_id TEXT,fingerprint TEXT,status TEXT,queued_at REAL,available_at REAL,
 attempts INTEGER DEFAULT 0,started_at REAL,finished_at REAL,error TEXT,output TEXT,validation TEXT,evaluation TEXT,usage TEXT,duration REAL
);
CREATE INDEX IF NOT EXISTS assistant_jobs_status ON assistant_jobs(status,available_at);
CREATE TABLE IF NOT EXISTS assistant_attempts (job_id TEXT,attempt INTEGER,status TEXT,started_at REAL,finished_at REAL,usage TEXT,error TEXT,PRIMARY KEY(job_id,attempt));
CREATE TABLE IF NOT EXISTS assistant_requests (id TEXT PRIMARY KEY,job_id TEXT,attempt INTEGER,role TEXT,model TEXT,started_at REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS assistant_tool_events (job_id TEXT,name TEXT,outcome TEXT,created_at REAL);
CREATE TABLE IF NOT EXISTS assistant_memory (id TEXT,version INTEGER,kind TEXT,status TEXT,payload TEXT,PRIMARY KEY(id,version));
CREATE VIRTUAL TABLE IF NOT EXISTS assistant_memory_index USING fts5(id UNINDEXED,text);
CREATE TABLE IF NOT EXISTS assistant_receipts (key TEXT PRIMARY KEY,fingerprint TEXT,result TEXT);
CREATE TABLE IF NOT EXISTS assistant_decisions (job_id TEXT,id TEXT,payload TEXT,PRIMARY KEY(job_id,id));
"""
