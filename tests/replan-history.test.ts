import assert from 'node:assert/strict';
import test from 'node:test';
import Database from 'better-sqlite3';
import { preserveMissionTaskEventsForReplan } from '../src/replan-history.js';

test('semantic replan preserves task recovery events before deleting the old DAG',()=>{
  const database=new Database(':memory:');
  database.pragma('foreign_keys=ON');
  database.exec(`
    CREATE TABLE missions (id TEXT PRIMARY KEY);
    CREATE TABLE mission_tasks (
      id TEXT PRIMARY KEY,
      mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE
    );
    CREATE TABLE mission_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
      task_id TEXT REFERENCES mission_tasks(id) ON DELETE CASCADE,
      event_type TEXT NOT NULL
    );
    INSERT INTO missions(id) VALUES ('mission_fixture');
    INSERT INTO mission_tasks(id,mission_id) VALUES ('task_fixture','mission_fixture');
    INSERT INTO mission_events(mission_id,task_id,event_type)
      VALUES ('mission_fixture','task_fixture','lead.task_recovered');
    INSERT INTO mission_events(mission_id,task_id,event_type)
      VALUES ('mission_fixture',NULL,'mission.planning_failed');
  `);

  const detached=preserveMissionTaskEventsForReplan(database,'mission_fixture');
  database.prepare('DELETE FROM mission_tasks WHERE mission_id=?').run('mission_fixture');

  const events=database.prepare(`
    SELECT task_id,event_type FROM mission_events
    WHERE mission_id=? ORDER BY id
  `).all('mission_fixture') as Array<{task_id:string|null;event_type:string}>;

  assert.equal(detached,1);
  assert.deepEqual(events,[
    {task_id:null,event_type:'lead.task_recovered'},
    {task_id:null,event_type:'mission.planning_failed'},
  ]);
  assert.equal(
    database.prepare(`
      SELECT COUNT(*) AS count FROM mission_events
      WHERE mission_id=? AND event_type='lead.task_recovered'
    `).get('mission_fixture')?.count,
    1,
  );

  database.close();
});
