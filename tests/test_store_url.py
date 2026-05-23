"""URL parsing — CONTRACT v0.8 #2."""

import pytest

from activegraph.store.url import InvalidStoreURL, parse_store_url


class TestParseStoreURL:
    def test_sqlite_absolute_path_four_slashes(self):
        # SQLAlchemy convention: 4 slashes = absolute (the fourth slash
        # is the leading slash of the absolute path).
        u = parse_store_url("sqlite:////tmp/run.db")
        assert u.scheme == "sqlite"
        assert u.sqlite_path == "/tmp/run.db"

    def test_sqlite_relative_path_three_slashes(self):
        u = parse_store_url("sqlite:///relative/path.db")
        assert u.scheme == "sqlite"
        assert u.sqlite_path == "relative/path.db"

    def test_sqlite_relative_with_dot(self):
        u = parse_store_url("sqlite:///./relative/path.db")
        assert u.scheme == "sqlite"
        assert u.sqlite_path == "./relative/path.db"

    def test_postgres_with_user_pass_host_db(self):
        u = parse_store_url("postgres://u:p@host:5432/dbname")
        assert u.scheme == "postgres"
        assert u.raw == "postgres://u:p@host:5432/dbname"

    def test_postgresql_alias(self):
        u = parse_store_url("postgresql://localhost/db")
        assert u.scheme == "postgres"

    def test_bare_path_rejected_with_helpful_message(self):
        with pytest.raises(InvalidStoreURL) as exc:
            parse_store_url("run.db")
        msg = str(exc.value)
        assert "no scheme" in msg
        assert "sqlite:///run.db" in msg

    def test_empty_url(self):
        with pytest.raises(InvalidStoreURL):
            parse_store_url("")

    def test_unsupported_scheme(self):
        with pytest.raises(InvalidStoreURL) as exc:
            parse_store_url("mysql://host/db")
        assert "mysql" in str(exc.value).lower()

    def test_sqlite_missing_path(self):
        with pytest.raises(InvalidStoreURL):
            parse_store_url("sqlite://")

    def test_postgres_missing_host(self):
        with pytest.raises(InvalidStoreURL):
            parse_store_url("postgres://")

    def test_falkor_basic(self):
        u = parse_store_url("falkor://localhost:6379/mygraph")
        assert u.scheme == "falkordb"
        assert u.falkordb_graph == "mygraph"
        assert u.raw == "falkor://localhost:6379/mygraph"

    def test_falkordb_alias(self):
        u = parse_store_url("falkordb://host/mygraph")
        assert u.scheme == "falkordb"
        assert u.falkordb_graph == "mygraph"

    def test_falkor_default_graph_when_path_omitted(self):
        u = parse_store_url("falkor://localhost:6379")
        assert u.scheme == "falkordb"
        assert u.falkordb_graph == "activegraph"

    def test_falkor_with_credentials(self):
        u = parse_store_url("falkor://user:pass@host:6379/g")
        assert u.scheme == "falkordb"
        assert u.falkordb_graph == "g"

    def test_falkor_missing_host_rejected(self):
        with pytest.raises(InvalidStoreURL) as exc:
            parse_store_url("falkor:///mygraph")
        msg = str(exc.value).lower()
        assert "no host" in msg or "host" in msg
