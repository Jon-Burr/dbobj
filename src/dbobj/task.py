from .json_store import MutableJSONAssocStore
from .database import AssocDatabase, ColumnDesc
import json

class TaskDB(AssocDatabase):
    def __init__(self, db_file):
        super(TaskDB, self).__init__(MutableJSONAssocStore(
            [c.name for c in self._columns], db_file) )

    taskID = ColumnDesc(is_index=True, store_type=str, type=int)
    taskName = ColumnDesc()

    def write(self):
        self._store.write()


if __name__ == "__main__":
    db = TaskDB("test.json")
    # t1 = db.add(taskID = 1005, taskName='test.1')
    # db.write()
