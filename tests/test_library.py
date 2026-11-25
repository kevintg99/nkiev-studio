def test_product_photo_only_in_the_scene_that_talks_about_it(tmp_path):
    """Vídeo das 9 baterias: a foto da Optima entrou em "Number four, the type" e a da Kirkland em "cold cranking
    amps"; gastas ali, faltaram na seção de cada uma. "say" no .json prende a foto à cena que fala do produto (ou à
    seção do ranking dele); "say_not" tira da seção de um produto parecido."""
    import json

    from broll_bot.models import Candidate, MediaType
    from broll_bot.providers.local_library import fits_scene

    def photo(name, **meta):
        f = tmp_path / f"{name}.jpg"
        f.write_bytes(b"x")
        (tmp_path / f"{name}.json").write_text(json.dumps(meta), encoding="utf-8")
        return Candidate(provider="mixkit_local", media_id=name, media_type=MediaType.IMAGE, duration=None,
                         thumbnail_url=None, download_url=None, width=1920, height=1080, local_path=str(f))

    optima = photo("optima", say=["Optima", "YellowTop"])
    kirkland = photo("kirkland", say=["Kirkland"], say_not=["Kirkland Signature AGM"])
    road = photo("winter-road")
    assert fits_scene(optima, "It's the Optima YellowTop, a dual-purpose A-G-M. Optima YellowTop")
    assert not fits_scene(optima, "Number four, the type. Most batteries are the regular kind.")
    assert fits_scene(optima, "For that, you get six hundred twenty cold cranking amps.", "Optima YellowTop")  # seção
    assert fits_scene(kirkland, "And number one is Costco's Kirkland Signature.", "Kirkland Signature")
    assert not fits_scene(kirkland, "Number one, cold cranking amps. You'll see it written as C-C-A.")
    assert not fits_scene(kirkland, "It's Costco's Kirkland Signature group 35 AGM.", "Kirkland Signature AGM")
    # citada de passagem na seção de outro produto: fica livre para a seção dela (a O-E Plus faltou na #2)
    oeplus = photo("oeplus", say=["O-E Plus"])
    assert not fits_scene(oeplus, "And it's worse than the next MotoMaster up, the O-E Plus.", "MotoMaster")
    assert fits_scene(oeplus, "Twenty-three dollars more gets you ninety more amps.", "MotoMaster O-E Plus")
    assert fits_scene(oeplus, "So far: the O-E Plus is the better buy.")  # resumo fora das seções
    assert fits_scene(road, "anything at all")


def test_requires_is_checked_once_per_file_not_on_every_search(tmp_path, monkeypatch):
    # 62% do tempo da busca das 9 baterias: o "requires" de 250 arquivos procurado no roteiro inteiro a cada busca
    from PIL import Image

    from broll_bot.models import MediaType
    from broll_bot.providers.local_library import LocalLibraryProvider

    Image.new("RGB", (640, 360)).save(tmp_path / "spinlaunch-launch.jpg")
    (tmp_path / "spinlaunch-launch.json").write_text('{"requires": ["SpinLaunch"]}', encoding="utf-8")
    prov = LocalLibraryProvider(tmp_path, tmp_path / ".idx" / "i.json")
    prov.context = "A SpinLaunch lançou o protótipo."
    checks = []
    original = prov._check_requires
    monkeypatch.setattr(prov, "_check_requires", lambda c: checks.append(c) or original(c))
    for _ in range(5):
        assert prov.search("spinlaunch launch", MediaType.IMAGE, 5)
    assert len(checks) == 1
    prov.context = "Um vídeo sobre carvão."  # outro roteiro: confere de novo
    assert prov.search("spinlaunch launch", MediaType.IMAGE, 5) == []
