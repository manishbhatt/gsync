from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast
import yaml
import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


if TYPE_CHECKING:
    from googleapiclient._apis.tasks.v1 import TasksResource, Task

logging.basicConfig(
    level=logging.ERROR, format="%(asctime)s [%(levelname)s] %(message)s"
)
# Google Tasks API scopes
SCOPES = ["https://www.googleapis.com/auth/tasks"]


def dbg[T](x: T) -> T:
    print(x)
    return x


class Config(TypedDict):
    directory_paths: list[str]
    daily_path: str


HOME_DIR = Path(os.environ["HOME"])
GOT_DIR = HOME_DIR / ".got"


def authenticate_google_tasks() -> TasksResource:
    logging.info("Authenticating with Google Tasks")
    creds = None
    token_path = GOT_DIR / "token.pickle"
    credentials_path = GOT_DIR / "credentials.json"

    # The file token.pickle stores the user's access and refresh tokens
    if os.path.exists(token_path):
        with open(token_path, "rb") as token:
            creds = pickle.load(token)
    # If there are no valid credentials, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials back to token.pickle
        with open(token_path, "wb") as token:
            pickle.dump(creds, token)

    service = build("tasks", "v1", credentials=creds)
    logging.info("Authentication successful")
    return service


def get_tasklist_id(service: TasksResource, tasklist_name: str) -> str:
    tasklists = service.tasklists().list().execute()

    tasklist = next(
        (t for t in tasklists.get("items", []) if t.get("title") == tasklist_name),
        None,
    )

    if tasklist is None:
        tasklist = service.tasklists().insert(body={"title": tasklist_name}).execute()

    return tasklist.get("id", "")


def read_local_tasks(markdown_file: Path) -> dict[str, bool]:
    with open(markdown_file, "r") as file:
        content = file.readlines()
    tasks = dict[str, bool]()
    for line in content:
        if line.startswith("- [x] ") or line.startswith("- [ ] "):
            task_title = line[6:].strip()
            tasks[task_title] = line[3] == "x"
    return tasks


def read_google_tasks(
    service: TasksResource, tasklist_id: str, *, parent: str | None = None
) -> tuple[list[Task], dict[str, bool]]:
    service_tasks = cast("TasksResource.TasksResource", service.tasks())
    tasks = (
        service_tasks.list(tasklist=tasklist_id, showCompleted=True)
        .execute()
        .get("items", [])
    )
    tasks = [task for task in tasks if task.get("parent") == parent]
    task_dict = {
        task["title"]: task.get("status") == "completed"
        for task in tasks
        if "title" in task
    }
    return tasks, task_dict


def merge_task_dicts(
    local_task_dict: dict[str, bool], google_task_dict: dict[str, bool]
) -> dict[str, bool]:
    merged_task_dict = local_task_dict.copy()
    for title, completed in google_task_dict.items():
        if (title not in merged_task_dict) or completed:
            merged_task_dict[title] = completed
    return merged_task_dict


# Create a function to mark tasks as completed in the markdown file based on task parameters, The task title of task and file should match
def update_local_tasks(markdown_file: Path, task_dict: dict[str, bool]):
    with open(markdown_file, "r") as file:
        content = file.readlines()
    with open(markdown_file, "w") as file:
        task_dict = task_dict.copy()

        for line in content:
            if (line.startswith("- [ ] ") or line.startswith("- [x] ")) and (
                (completed := task_dict.pop((task_title := line[6:].strip()), None))
                is not None
            ):
                file.write(f"- [{'x' if completed else ' '}] {task_title}\n")
            else:
                file.write(line)

        for title, completed in task_dict.items():
            file.write(f"- [{'x' if completed else ' '}] {title}\n")


def update_google_tasks(
    service: TasksResource,
    tasklist_id: str,
    google_tasks: list[Task],
    task_dict: dict[str, bool],
    *,
    parent: str | None = None,
):
    service_tasks = cast("TasksResource.TasksResource", service.tasks())

    for google_task in google_tasks:
        google_completed = google_task.get("status") == "completed"
        merged_completed = task_dict.pop(google_task.get("title", ""))

        if merged_completed and not google_completed:
            google_task["status"] = "completed"
            service_tasks.update(
                tasklist=tasklist_id, task=google_task.get("id", ""), body=google_task
            ).execute()

    if parent is not None:
        for title, completed in task_dict.items():
            service_tasks.insert(
                tasklist=tasklist_id,
                parent=parent,
                body={
                    "title": title,
                    "status": "completed" if completed else "needsAction",
                },
            ).execute()
    else:
        for title, completed in task_dict.items():
            service_tasks.insert(
                tasklist=tasklist_id,
                body={
                    "title": title,
                    "status": "completed" if completed else "needsAction",
                },
            ).execute()


def sync_tasks(service: TasksResource, markdown_file: Path):
    local_task_dict = read_local_tasks(markdown_file)

    # task list name is the file name without the extension
    tasklist_id = get_tasklist_id(service, markdown_file.stem)
    google_tasks, google_task_dict = read_google_tasks(service, tasklist_id)

    merged_task_dict = merge_task_dicts(local_task_dict, google_task_dict)

    update_local_tasks(markdown_file, merged_task_dict)
    update_google_tasks(service, tasklist_id, google_tasks, merged_task_dict)


def sync_daily_tasks(service: TasksResource, markdown_file: Path):
    service_tasks = cast("TasksResource.TasksResource", service.tasks())

    # get tasks from the markdown file
    local_task_dict = read_local_tasks(markdown_file)

    # get task list "Daily"
    tasklist_id = get_tasklist_id(service, "Daily")

    # get task named date
    google_tasks, _ = read_google_tasks(service, tasklist_id)

    task = next(
        (t for t in google_tasks if t.get("title") == markdown_file.stem),
        None,
    )

    if task is None:
        task = service_tasks.insert(
            tasklist=tasklist_id, body={"title": markdown_file.stem}
        ).execute()

    task_id = task.get("id", "")

    google_subtasks, google_task_dict = read_google_tasks(
        service, tasklist_id, parent=task_id
    )

    merged_task_dict = merge_task_dicts(local_task_dict, google_task_dict)

    update_local_tasks(markdown_file, merged_task_dict)

    update_google_tasks(
        service, tasklist_id, google_subtasks, merged_task_dict, parent=task_id
    )


def main():
    config_path = GOT_DIR / "config.yaml"
    with open(config_path, "r") as filename:
        logging.info(f"Reading configuration from {config_path}")
        config: Config = yaml.safe_load(filename)

    service = authenticate_google_tasks()

    for directory_path in map(Path, config["directory_paths"]):
        logging.info(f"Processing directory: {directory_path}")

        # get list of all files in the directory with .md extension. Get full path of the files
        for filename in map(Path, os.listdir(directory_path)):
            if not filename.suffix == ".md":
                continue

            filename = directory_path / filename

            logging.info(f"Processing markdown file: {filename}")

            sync_tasks(service, filename)

    daily_path = Path(config["daily_path"])

    logging.info(f"Processing daily markdown file: {daily_path}")

    for google_daily_task in read_google_tasks(
        service, get_tasklist_id(service, "Daily")
    )[0]:
        google_daily_task_title = google_daily_task.get("title", "")
        markdown_file = daily_path / f"{google_daily_task_title}.md"

        if not markdown_file.exists():
            with open(markdown_file, "w"):
                pass

    for filename in map(Path, os.listdir(daily_path)):
        if not filename.suffix == ".md":
            continue

        filename = daily_path / filename

        logging.info(f"Processing markdown file: {filename}")

        sync_daily_tasks(service, filename)


if __name__ == "__main__":
    main()
