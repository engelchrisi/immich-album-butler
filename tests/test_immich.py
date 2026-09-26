import datetime as dt
import unittest

from immich_album_butler.immich import Asset, ImmichClient, ImmichError

from .stub_immich import API_KEY, StubImmich, day, fake_id, make_asset


class AssetParsingTests(unittest.TestCase):
    def test_local_wall_clock_time_wins_over_utc(self):
        asset = Asset.from_api({"id": fake_id(1), "localDateTime": "2019-07-04T17:58:53.460Z",
                                "exifInfo": {"dateTimeOriginal": "2019-07-04T21:58:53+00:00"}})
        self.assertEqual(asset.taken_at, dt.datetime(2019, 7, 4, 17, 58, 53, 460000))

    def test_an_asset_without_coordinates_is_unlocated(self):
        asset = Asset.from_api({"id": fake_id(1), "localDateTime": day(2019, 7, 4),
                                "exifInfo": {"city": None}})
        self.assertFalse(asset.located)

    def test_missing_exif_does_not_raise(self):
        asset = Asset.from_api({"id": fake_id(1), "localDateTime": day(2019, 7, 4)})
        self.assertIsNone(asset.country)

    def test_the_folder_and_camera_are_read(self):
        asset = Asset.from_api({"id": fake_id(1), "localDateTime": day(2019, 7, 4),
                                "originalPath": "/photos/Italy 2019/IMG_0001.jpg",
                                "exifInfo": {"make": "Canon", "model": "EOS R"}})
        self.assertEqual(asset.original_path, "/photos/Italy 2019/IMG_0001.jpg")
        self.assertEqual(asset.camera, "Canon EOS R")

    def test_no_exif_means_no_camera(self):
        asset = Asset.from_api({"id": fake_id(1), "localDateTime": day(2019, 7, 4)})
        self.assertIsNone(asset.camera)
        self.assertEqual(asset.original_path, "")

    def test_videos_are_recognised(self):
        asset = Asset.from_api({"id": fake_id(1), "type": "VIDEO",
                                "localDateTime": day(2019, 7, 4)})
        self.assertTrue(asset.is_video)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.assets = [
            make_asset(1, when=day(2019, 7, 1), lat=41.9, lon=12.5,
                       city="Rome", state="Lazio", country="Italy"),
            make_asset(2, when=day(2019, 7, 2), lat=41.9, lon=12.5,
                       city="Rome", state="Lazio", country="Italy"),
            make_asset(3, when=day(2019, 7, 3)),
            make_asset(4, when=day(2020, 1, 1), lat=40.4, lon=-3.7,
                       city="Madrid", country="Spain"),
        ]

    def test_search_pages_through_every_result(self):
        with StubImmich(self.assets) as stub:
            client = ImmichClient(stub.url, API_KEY)
            found = list(client.search_metadata(page_size=2))
        self.assertEqual(len(found), 4)
        # Two pages of two; the second says there is no next page, so the
        # client stops rather than asking for an empty third.
        self.assertEqual(sum(1 for _, path in stub.requests
                             if path == "/api/search/metadata"), 2)

    def test_a_single_page_does_not_ask_for_another(self):
        with StubImmich(self.assets) as stub:
            list(ImmichClient(stub.url, API_KEY).search_metadata(page_size=100))
        self.assertEqual(len(stub.requests), 1)

    def test_date_filters_are_inclusive_at_both_ends(self):
        with StubImmich(self.assets, page_size=100) as stub:
            found = list(ImmichClient(stub.url, API_KEY).search_metadata(
                taken_after=dt.date(2019, 7, 1), taken_before=dt.date(2019, 7, 3)))
        self.assertEqual({a.id for a in found},
                         {fake_id(1), fake_id(2), fake_id(3)})

    def test_an_empty_library_yields_nothing(self):
        with StubImmich([], page_size=2) as stub:
            self.assertEqual(list(ImmichClient(stub.url, API_KEY).search_metadata()), [])

    def test_a_wrong_key_is_reported_as_such(self):
        with StubImmich(self.assets) as stub:
            with self.assertRaises(ImmichError) as caught:
                list(ImmichClient(stub.url, "wrong-key").search_metadata())
        self.assertEqual(caught.exception.status, 401)
        self.assertIn("rejected the API key", str(caught.exception))

    def test_a_missing_permission_names_the_scope(self):
        with StubImmich(self.assets, missing_permissions={"album.read"}) as stub:
            with self.assertRaises(ImmichError) as caught:
                ImmichClient(stub.url, API_KEY).albums()
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("album.read", str(caught.exception))

    def test_an_unreachable_server_says_so(self):
        client = ImmichClient("http://127.0.0.1:1", API_KEY, timeout=2)
        with self.assertRaises(ImmichError) as caught:
            client.server_version()
        self.assertIn("cannot reach Immich", str(caught.exception))

    def test_an_empty_key_is_refused_before_any_request(self):
        with self.assertRaises(ImmichError):
            ImmichClient("http://immich.example.lan:2283", "")


class PeopleTests(unittest.TestCase):
    """A real library has one person per face cluster: thousands, mostly unnamed."""

    PEOPLE = [{"id": "person-alex", "name": "Alex"},
              {"id": "person-sam", "name": "Sam"}]

    def test_only_named_people_come_back(self):
        with StubImmich([], people=self.PEOPLE, unnamed_people=5) as stub:
            found = ImmichClient(stub.url, API_KEY).people()
        self.assertEqual([p.name for p in found], ["Alex", "Sam"])

    def test_paging_uses_has_next_page_not_next_page(self):
        """The people endpoint pages differently from search/metadata."""
        many = [{"id": f"person-{n}", "name": f"Name {n}"} for n in range(1200)]
        with StubImmich([], people=many) as stub:
            found = ImmichClient(stub.url, API_KEY).people()
        self.assertEqual(len(found), 1200)

    def test_it_stops_once_the_named_people_run_out(self):
        with StubImmich([], people=self.PEOPLE, unnamed_people=5000) as stub:
            found = ImmichClient(stub.url, API_KEY).people()
            calls = sum(1 for _, path in stub.requests if path == "/api/people")
        self.assertEqual(len(found), 2)
        # Page 1 holds the two named ones; page 2 has none, so it stops there
        # instead of walking all eleven pages of face clusters.
        self.assertEqual(calls, 2)

    def test_a_name_can_be_looked_up_directly(self):
        with StubImmich([], people=self.PEOPLE, unnamed_people=5000) as stub:
            found = ImmichClient(stub.url, API_KEY).find_people("alex")
        self.assertEqual([p.id for p in found], ["person-alex"])

    def test_a_partial_name_is_not_an_exact_match(self):
        with StubImmich([], people=self.PEOPLE) as stub:
            self.assertEqual(ImmichClient(stub.url, API_KEY).find_people("Ale"), [])

    def test_an_unknown_name_finds_nobody(self):
        with StubImmich([], people=self.PEOPLE) as stub:
            self.assertEqual(ImmichClient(stub.url, API_KEY).find_people("Nobody"), [])


class AlbumWriteTests(unittest.TestCase):
    def test_creating_an_album_returns_its_id_and_name(self):
        with StubImmich([]) as stub:
            album = ImmichClient(stub.url, API_KEY).create_album("Italy 2019")
        self.assertEqual(album.name, "Italy 2019")
        self.assertTrue(album.id)

    def test_adding_assets_reports_how_many_were_new(self):
        with StubImmich([]) as stub:
            client = ImmichClient(stub.url, API_KEY)
            album = client.create_album("Italy 2019")
            first = client.add_assets(album.id, [fake_id(1), fake_id(2)])
            again = client.add_assets(album.id, [fake_id(1), fake_id(3)])
        self.assertEqual(first, 2)
        self.assertEqual(again, 1)          # the duplicate is not counted

    def test_a_large_add_is_split_into_chunks(self):
        ids = [fake_id(n) for n in range(0, 60)]
        with StubImmich([]) as stub:
            client = ImmichClient(stub.url, API_KEY)
            album = client.create_album("Big")
            import immich_album_butler.immich as immich_module
            original = immich_module.ADD_CHUNK
            immich_module.ADD_CHUNK = 25
            try:
                added = client.add_assets(album.id, ids)
            finally:
                immich_module.ADD_CHUNK = original
        self.assertEqual(added, 60)
        puts = [p for method, p in stub.requests if method == "PUT"]
        self.assertEqual(len(puts), 3)

    def test_album_contents_are_enumerated_not_read_from_the_album_object(self):
        """The album endpoint returns no asset list, so this must search."""
        library = [make_asset(1, when=day(2019, 7, 1)),
                   make_asset(2, when=day(2019, 7, 2)),
                   make_asset(3, when=day(2019, 7, 3))]
        with StubImmich(library, page_size=2) as stub:
            client = ImmichClient(stub.url, API_KEY)
            album = client.create_album("Italy 2019",
                                        asset_ids=[fake_id(1), fake_id(2)])
            found = client.album_asset_ids(album.id)
        self.assertEqual(found, {fake_id(1), fake_id(2)})

    def test_an_empty_album_reports_no_assets(self):
        with StubImmich([make_asset(1, when=day(2019, 7, 1))]) as stub:
            client = ImmichClient(stub.url, API_KEY)
            album = client.create_album("Empty")
            self.assertEqual(client.album_asset_ids(album.id), set())

    def test_the_album_display_count_is_not_used_to_decide_what_is_missing(self):
        """Immich's assetCount collapses stacked pairs and under-reports."""
        library = [make_asset(n, when=day(2019, 7, n)) for n in range(1, 4)]
        ids = [fake_id(n) for n in range(1, 4)]
        with StubImmich(library, page_size=2, stacked_pairs=1) as stub:
            client = ImmichClient(stub.url, API_KEY)
            album = client.create_album("Italy 2019", asset_ids=ids)
            listed = client.album_asset_ids(album.id)
            reported = next(a.asset_count for a in client.albums()
                            if a.id == album.id)
        self.assertEqual(len(listed), 3)
        self.assertEqual(reported, 2)       # lower, and rightly ignored

    def test_removing_assets_leaves_the_rest_in_place(self):
        library = [make_asset(1, when=day(2019, 7, 1)),
                   make_asset(2, when=day(2019, 7, 2))]
        with StubImmich(library, page_size=100) as stub:
            client = ImmichClient(stub.url, API_KEY)
            album = client.create_album("Italy 2019",
                                        asset_ids=[fake_id(1), fake_id(2)])
            client.remove_assets(album.id, [fake_id(1)])
            remaining = client.album_asset_ids(album.id)
        self.assertEqual(remaining, {fake_id(2)})


if __name__ == "__main__":
    unittest.main()
