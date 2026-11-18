"""Tests for the Haiku object-parser sub-agent, /api/objects/parse-prompt/,
SpaceObject description/referencePins serialization, and explicit Pinterest
image references in search."""
import json
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from .models import Placement, Space, SpaceInspiration, SpaceObject, VibeBoard
from .searcher import engine, prompts as searcher_prompts, query_builder
from .test_like_search import FAKE_CLAUDE, FAKE_UCP
from .vibes import matching, paths


def _write_jpeg(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (32, 32), (240, 238, 232)).save(path, 'JPEG')


class ObjectParserTests(TestCase):
    def setUp(self):
        self.var = Path(tempfile.mkdtemp(prefix='object-parser-'))
        self.agent_cwd = self.var / 'agent-cwd'
        self.addCleanup(shutil.rmtree, self.var, True)
        cm = override_settings(
            SEARCHER_VAR_DIR=self.var,
            VIBES_DIR=self.var / 'vibes',
            SEARCHER_AGENT_CWD=self.agent_cwd,
            SEARCHER_CLAUDE_BIN=FAKE_CLAUDE,
            SEARCHER_UCP_BIN=FAKE_UCP,
            SEARCHER_AGENT='claude',
            OBJECT_PARSER_MODEL='haiku',
        )
        cm.enable()
        self.addCleanup(cm.disable)

        now = timezone.now()
        self.board = VibeBoard.objects.create(
            board_url='https://www.pinterest.com/tester/living-room/',
            resolved_url='https://www.pinterest.com/tester/living-room/',
            title='Living room vibes',
        )
        self.space = Space.objects.create(
            id='space_living',
            name='Living room',
            type='floor',
            unit='in',
            dims={'x': 144, 'y': 120},
            vibe_board=self.board,
            created_at=now,
            updated_at=now,
        )
        self.rank = 0

    def add_pin(
        self,
        pin_id,
        *,
        item='sofa',
        categories=('furniture',),
        styles=('japandi',),
        colors=('white',),
        materials=('fabric',),
        description='white boucle sofa',
    ):
        self.rank += 1
        rel = f'boards/{self.board.id}/{pin_id}.jpg'
        _write_jpeg(paths.vibes_dir() / rel)
        return SpaceInspiration.objects.create(
            board=self.board,
            pin_id=pin_id,
            pin_url=f'https://www.pinterest.com/pin/{pin_id}/',
            image_url=f'https://i.pinimg.com/736x/{pin_id}.jpg',
            image_path=rel,
            board_rank=self.rank,
            categories=list(categories),
            item=item,
            styles=list(styles),
            colors=list(colors),
            materials=list(materials),
            description=description,
            item_box=[0.1, 0.1, 0.9, 0.9],
            categorized_at=timezone.now(),
        )

    def post_json(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type='application/json')

    def patch_json(self, url, body):
        return self.client.patch(url, data=json.dumps(body), content_type='application/json')

    def test_japandi_boucle_couch_prompt_via_haiku_subagent(self):
        self.add_pin('pin_sofa_1', item='sofa', styles=['japandi'], colors=['white'], materials=['boucle', 'fabric'])
        self.add_pin('pin_table_1', item='dining table', styles=['modern'], colors=['brown'], materials=['wood'], description='oak dining table')
        self.add_pin('pin_rug_1', item='area rug', categories=['rug'], styles=['japandi'], colors=['cream'], materials=['wool'])

        prompt_text = 'Japandi style white fabric couch with bouclé cushioning.'
        res = self.post_json(
            '/api/objects/parse-prompt/',
            {'prompt': prompt_text, 'spaceIds': [self.space.id]},
        )
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['agentMode'], 'claude')

        parsed = body['parsed']
        self.assertEqual(parsed['name'], 'Japandi white bouclé couch')
        self.assertEqual(parsed['description'], prompt_text)
        self.assertEqual(parsed['type'], 'furniture')
        self.assertEqual(parsed['material'], 'fabric')
        # Japandi is NOT in Style choices; must never be forced to scandinavian.
        self.assertIsNone(parsed['style'])
        self.assertIsNone(parsed['weight'])
        self.assertIsNone(parsed['priceMin'])
        self.assertIsNone(parsed['priceLimit'])
        self.assertIsNone(parsed['minRating'])
        self.assertEqual(parsed['similarItems'], [])
        self.assertEqual(parsed['dims'], {'x': 90.0, 'y': 40.0})

        # Sofa pin is recommended in similarPins; dining table stays in categoryPins; rug is excluded.
        self.assertEqual([p['pinId'] for p in body['similarPins']], ['pin_sofa_1'])
        self.assertEqual([p['pinId'] for p in body['categoryPins']], ['pin_table_1'])

        # Output creates a valid SpaceObject directly via POST /api/objects/.
        create_res = self.post_json(
            '/api/objects/',
            {**parsed, 'referencePins': ['pin_sofa_1']},
        )
        self.assertEqual(create_res.status_code, 201, create_res.content)
        created = create_res.json()
        self.assertEqual(created['description'], prompt_text)
        self.assertEqual(created['referencePins'], ['pin_sofa_1'])

    def test_post_guards_strip_hallucinated_enums_and_numbers(self):
        res = self.post_json(
            '/api/objects/parse-prompt/',
            {
                'prompt': 'Japandi style white fabric couch fake=hallucinate',
                'spaceIds': [self.space.id],
            },
        )
        self.assertEqual(res.status_code, 200, res.content)
        parsed = res.json()['parsed']
        self.assertIsNone(parsed['style'])
        self.assertIsNone(parsed['weight'])
        self.assertIsNone(parsed['priceMin'])
        self.assertIsNone(parsed['priceLimit'])
        self.assertIsNone(parsed['minRating'])
        self.assertEqual(parsed['similarItems'], [])
        # Unstated z height and fake explicit dims must not survive.
        self.assertNotIn('z', parsed['dims'])
        self.assertEqual(parsed['dims'], {'x': 90.0, 'y': 40.0})

    def test_drawn_dims_preserved_when_prompt_omits_size_and_clamped_otherwise(self):
        res_drawn = self.post_json(
            '/api/objects/parse-prompt/',
            {
                'prompt': 'Japandi style white fabric couch with bouclé cushioning.',
                'spaceIds': [self.space.id],
                'drawnDims': {'x': 76, 'y': 34},
            },
        )
        self.assertEqual(res_drawn.status_code, 200)
        self.assertEqual(res_drawn.json()['parsed']['dims'], {'x': 76.0, 'y': 34.0})

        now = timezone.now()
        nook = Space.objects.create(
            id='space_nook',
            name='Small nook',
            type='floor',
            unit='in',
            dims={'x': 60, 'y': 32},
            created_at=now,
            updated_at=now,
        )
        res_clamped = self.post_json(
            '/api/objects/parse-prompt/',
            {
                'prompt': 'Japandi style white fabric couch with bouclé cushioning.',
                'spaceIds': [nook.id],
            },
        )
        self.assertEqual(res_clamped.status_code, 200)
        self.assertEqual(res_clamped.json()['parsed']['dims'], {'x': 60.0, 'y': 32.0})

    def test_cli_failure_falls_back_to_deterministic_parser(self):
        self.add_pin('pin_sofa_det', item='couch', styles=['japandi'], colors=['white'], materials=['fabric'])
        res = self.post_json(
            '/api/objects/parse-prompt/',
            {
                'prompt': '72 x 34 inch mid-century walnut desk under $1200 fake=exit1',
                'spaceIds': [self.space.id],
            },
        )
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body['agentMode'], 'deterministic')
        parsed = body['parsed']
        self.assertEqual(parsed['type'], 'furniture')
        self.assertEqual(parsed['style'], 'mid-century')
        self.assertEqual(parsed['material'], 'wood')
        self.assertEqual(parsed['dims'], {'x': 72.0, 'y': 34.0})
        self.assertEqual(parsed['priceLimit'], 1200.0)
        self.assertIsNone(parsed['priceMin'])

    def test_object_serializer_validates_description_and_reference_pins(self):
        created = self.post_json(
            '/api/objects/',
            {
                'name': 'Bouclé sofa',
                'description': 'Soft white bouclé 3-seater',
                'type': 'furniture',
                'dims': {'x': 84, 'y': 36},
                'referencePins': ['pin_1', 'pin_2', 'pin_1'],
            },
        )
        self.assertEqual(created.status_code, 201, created.content)
        obj_json = created.json()
        self.assertEqual(obj_json['description'], 'Soft white bouclé 3-seater')
        self.assertEqual(obj_json['referencePins'], ['pin_1', 'pin_2'])

        # Clearing description with null works via PATCH.
        patched = self.patch_json(
            f"/api/objects/{obj_json['id']}/",
            {'description': None, 'referencePins': ['pin_3']},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertNotIn('description', patched.json())
        self.assertEqual(patched.json()['referencePins'], ['pin_3'])

        # More than 5 reference pins fails validation.
        bad = self.patch_json(
            f"/api/objects/{obj_json['id']}/",
            {'referencePins': ['p1', 'p2', 'p3', 'p4', 'p5', 'p6']},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn('referencePins', bad.json()['details'])

    def test_explicit_reference_pins_override_auto_space_vibes_in_search(self):
        self.add_pin('pin_auto', item='sofa', styles=['industrial'], colors=['black'], materials=['leather'])
        self.add_pin('pin_picked', item='armchair', styles=['japandi'], colors=['white'], materials=['boucle'])

        now = timezone.now()
        obj = SpaceObject.objects.create(
            id='obj_couch',
            name='Japandi white bouclé couch',
            description='Japandi style white fabric couch with bouclé cushioning.',
            type='furniture',
            material='fabric',
            dims={'x': 88, 'y': 38},
            reference_pins=['pin_picked'],
            created_at=now,
        )
        Placement.objects.create(space=self.space, object=obj, x=10, y=10)

        run = engine.create_run(obj)
        self.assertIsNotNone(run.vibes)
        self.assertTrue(run.vibes.get('explicitReferences'))
        self.assertEqual([p['pinId'] for p in run.vibes['pins']], ['pin_picked'])
        self.assertEqual([p['pinId'] for p in run.vibes['photos']], ['pin_picked'])

        intent = query_builder.build_intent(run.object_snapshot)
        self.assertIn('Japandi style white fabric couch with bouclé cushioning.', intent)

        sys_prompt = searcher_prompts.system_prompt(run, vibe_photos=1)
        self.assertIn('EXPLICITLY selected these Pinterest pin(s)', sys_prompt)

        # With empty reference_pins, automatic space vibes match both furniture pins (sofa ranked first).
        obj.reference_pins = []
        obj.save(update_fields=['reference_pins'])
        auto_run = engine.create_run(obj)
        self.assertFalse(auto_run.vibes.get('explicitReferences', False))
        self.assertEqual(auto_run.vibes['matched'], 2)
        self.assertEqual(auto_run.vibes['pins'][0]['pinId'], 'pin_auto')
