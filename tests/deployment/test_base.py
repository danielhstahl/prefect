import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

import prefect
from prefect.deployments.base import (
    configure_project_by_recipe,
    find_prefect_directory,
    initialize_project,
    register_flow,
    _search_for_flow_functions,
    _find_flow_functions_in_file,
)

from prefect.settings import PREFECT_DEBUG_MODE, temporary_settings

TEST_PROJECTS_DIR = prefect.__development_base_path__ / "tests" / "test-projects"


@pytest.fixture(autouse=True)
def project_dir(tmp_path):
    original_dir = os.getcwd()
    if sys.version_info >= (3, 8):
        shutil.copytree(TEST_PROJECTS_DIR, tmp_path, dirs_exist_ok=True)
        (tmp_path / ".prefect").mkdir(exist_ok=True, mode=0o0700)
        os.chdir(tmp_path)
        yield tmp_path
    else:
        shutil.copytree(TEST_PROJECTS_DIR, tmp_path / "three-seven")
        (tmp_path / "three-seven" / ".prefect").mkdir(exist_ok=True, mode=0o0700)
        os.chdir(tmp_path / "three-seven")
        yield tmp_path / "three-seven"
    os.chdir(original_dir)


class TestFindProject:
    async def test_find_project_works_in_root(self, tmp_path):
        # make hidden .prefect/ directory in tmp_path
        (tmp_path / ".prefect").mkdir(exist_ok=True)
        assert find_prefect_directory(tmp_path) == tmp_path / ".prefect"

    async def test_find_project_works_in_subdir(self, tmp_path):
        # make hidden .prefect/ directory in tmp_path
        (tmp_path / ".prefect").mkdir(exist_ok=True)
        (tmp_path / "subdir" / "subsubdir").mkdir(parents=True)
        assert (
            find_prefect_directory(tmp_path / "subdir" / "subsubdir")
            == tmp_path / ".prefect"
        )


class TestRecipes:
    async def test_configure_project_by_recipe_raises(self):
        with pytest.raises(ValueError, match="Unknown recipe"):
            configure_project_by_recipe("not-a-recipe")

    @pytest.mark.parametrize(
        "recipe",
        [
            d.absolute().name
            for d in Path(
                prefect.__development_base_path__
                / "src"
                / "prefect"
                / "deployments"
                / "recipes"
            ).iterdir()
            if d.is_dir()
        ],
    )
    async def test_configure_project_by_recipe_doesnt_raise(self, recipe):
        recipe_config = configure_project_by_recipe(recipe)
        for key in ["name", "prefect-version", "build", "push", "pull"]:
            assert key in recipe_config

    @pytest.mark.parametrize(
        "recipe",
        [
            d.absolute().name
            for d in Path(
                prefect.__development_base_path__
                / "src"
                / "prefect"
                / "deployments"
                / "recipes"
            ).iterdir()
            if d.is_dir() and "git" in d.absolute().name
        ],
    )
    async def test_configure_project_handles_templates_on_git_recipes(self, recipe):
        recipe_config = configure_project_by_recipe(
            recipe, repository="test-org/test-repo"
        )
        clone_step = recipe_config["pull"][0]["prefect.deployments.steps.git_clone"]
        assert clone_step["repository"] == "test-org/test-repo"
        assert clone_step["branch"] == "{{ branch }}"

    async def test_configure_project_replaces_templates_on_docker(self):
        recipe_config = configure_project_by_recipe("docker", name="test-dir")
        clone_step = recipe_config["pull"][0][
            "prefect.deployments.steps.set_working_directory"
        ]
        assert clone_step["directory"] == "/opt/prefect/test-dir"


class TestInitProject:
    async def test_initialize_project_works(self):
        files = initialize_project()
        assert len(files) >= 2

        for file in files:
            assert Path(file).exists()

        # test defaults
        with open("prefect.yaml", "r") as f:
            contents = yaml.safe_load(f)

        assert contents["name"] is not None
        assert contents["prefect-version"] == prefect.__version__

    async def test_initialize_project_with_name(self):
        files = initialize_project(name="my-test-its-a-test")
        assert len(files) >= 2

        with open("prefect.yaml", "r") as f:
            contents = yaml.safe_load(f)

        assert contents["name"] == "my-test-its-a-test"

    async def test_initialize_project_with_recipe(self):
        files = initialize_project(recipe="docker-git")
        assert len(files) >= 2

        with open("prefect.yaml", "r") as f:
            contents = yaml.safe_load(f)

        clone_step = contents["pull"][0]
        assert "prefect.deployments.steps.git_clone" in clone_step

        build_step = contents["build"][0]
        assert "prefect_docker.deployments.steps.build_docker_image" in build_step

    @pytest.mark.parametrize(
        "recipe",
        [
            d.absolute().name
            for d in Path(
                prefect.__development_base_path__
                / "src"
                / "prefect"
                / "deployments"
                / "recipes"
            ).iterdir()
            if d.is_dir() and "docker" in d.absolute().name
        ],
    )
    async def test_initialize_project_with_docker_recipe_default_image(self, recipe):
        files = initialize_project(recipe=recipe)
        assert len(files) >= 2

        with open("prefect.yaml", "r") as f:
            contents = yaml.safe_load(f)

        build_step = contents["build"][0]
        assert "prefect_docker.deployments.steps.build_docker_image" in build_step

        assert (
            contents["deployments"][0]["work_pool"]["job_variables"]["image"]
            == "{{ build_image.image }}"
        )


class TestRegisterFlow:
    async def test_register_flow_works_in_root(self, project_dir):
        f = await register_flow(
            str(project_dir / "import-project" / "my_module" / "flow.py") + ":test_flow"
        )
        assert f.name == "test"

        flows_file = project_dir / ".prefect" / "flows.json"
        with flows_file.open(mode="r") as f:
            flows = json.load(f)

        assert flows["test"] == "import-project/my_module/flow.py:test_flow"

    async def test_register_flow_allows_identical_calls(self, project_dir):
        f = await register_flow(
            str(project_dir / "import-project" / "my_module" / "flow.py") + ":test_flow"
        )
        assert f.name == "test"

        f = await register_flow(
            str(project_dir / "import-project" / "my_module" / "flow.py") + ":test_flow"
        )
        assert f.name == "test"

    async def test_register_flow_disallows_overwrites(self, project_dir):
        f = await register_flow(
            str(project_dir / "import-project" / "my_module" / "flow.py") + ":test_flow"
        )
        assert f.name == "test"

        with pytest.raises(ValueError, match="Conflicting entry found"):
            await register_flow(
                str(project_dir / "import-project" / "my_module" / "flow.py")
                + ":prod_flow"
            )

        f = await register_flow(
            str(project_dir / "import-project" / "my_module" / "flow.py")
            + ":prod_flow",
            force=True,
        )
        assert f.name == "test"


class TestDiscoverFlows:
    async def test_find_all_flows_in_dir_tree(self, project_dir):
        flows = await _search_for_flow_functions(str(project_dir))
        assert len(flows) == 6
        assert sorted(flows, key=lambda f: f["function_name"]) == [
            {
                "flow_name": "foobar",
                "function_name": "foobar",
                "filepath": str(
                    project_dir / "nested-project" / "implicit_relative.py"
                ),
            },
            {
                "flow_name": "foobar",
                "function_name": "foobar",
                "filepath": str(
                    project_dir / "nested-project" / "explicit_relative.py"
                ),
            },
            {
                "flow_name": "my_flow",
                "function_name": "my_flow",
                "filepath": str(project_dir / "flows" / "hello.py"),
            },
            {
                "flow_name": "my_flow2",
                "function_name": "my_flow2",
                "filepath": str(project_dir / "flows" / "hello.py"),
            },
            {
                "flow_name": "prod_flow",
                "function_name": "prod_flow",
                "filepath": str(
                    project_dir / "import-project" / "my_module" / "flow.py"
                ),
            },
            {
                "flow_name": "test_flow",
                "function_name": "test_flow",
                "filepath": str(
                    project_dir / "import-project" / "my_module" / "flow.py"
                ),
            },
        ]

    async def test_find_all_flows_works_on_large_directory_structures(self):
        flows = await _search_for_flow_functions(str(prefect.__development_base_path__))
        assert len(flows) > 500

    async def test_find_flow_functions_in_file_returns_empty_list_on_file_error(
        self, caplog
    ):
        with temporary_settings({PREFECT_DEBUG_MODE: True}):
            assert await _find_flow_functions_in_file("foo.py") == []
            assert "Could not open foo.py" in caplog.text
