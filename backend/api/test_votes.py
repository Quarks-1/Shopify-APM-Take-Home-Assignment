"""Product votes: PUT/GET /api/objects/<id>/votes/, persistence, serializer."""
import json

from django.test import TestCase
from django.utils import timezone

from .models import Candidate, CatalogQuery, ProductVote, RunStatus, SearchRun, SpaceObject, Verdict
from .searcher import engine
from .serializers import object_to_json

GID = 'gid://shopify/p/abc'


def make_object(oid='obj1', **kw):
    fields = dict(
        id=oid, name='Loveseat', dims={'x': 60, 'y': 33, 'z': 34}, type='furniture',
        created_at=timezone.now(),
    )
    fields.update(kw)
    return SpaceObject.objects.create(**fields)


def make_candidate(obj, gid=GID, **kw):
    run = engine.create_run(obj)
    SearchRun.objects.filter(pk=run.pk).update(status=RunStatus.DONE)
    cq = CatalogQuery.objects.create(run=run, round=1, strategy='exact', payload={'query': 'x'})
    fields = dict(run=run, query=cq, product_gid=gid, title='Sofa', seller_name='Acme',
                  seller_domain='acme.com', price_cents=12900, currency='USD',
                  description='Soft', verdict=Verdict.ACCEPTED)
    fields.update(kw)
    return Candidate.objects.create(**fields)


class VoteTests(TestCase):
    def setUp(self):
        self.obj = make_object()
        self.cand = make_candidate(self.obj)
        self.url = f'/api/objects/{self.obj.pk}/votes/'

    def put(self, body, url=None):
        return self.client.put(url or self.url, json.dumps(body), content_type='application/json')

    def test_upsert_up_then_down(self):
        r = self.put({'candidateId': self.cand.pk, 'vote': 'up'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'objectId': 'obj1', 'votes': {GID: 'up'}})
        r = self.put({'candidateId': self.cand.pk, 'vote': 'down'})
        self.assertEqual(r.json()['votes'], {GID: 'down'})
        self.assertEqual(ProductVote.objects.count(), 1)
        snap = ProductVote.objects.get().product
        self.assertEqual(snap['title'], 'Sofa')
        self.assertEqual(snap['seller'], {'name': 'Acme', 'domain': 'acme.com'})
        self.assertEqual(snap['price'], 129)
        self.assertEqual(snap['productGid'], GID)

    def test_clear_with_null(self):
        self.put({'candidateId': self.cand.pk, 'vote': 'up'})
        r = self.put({'candidateId': self.cand.pk, 'vote': None})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['votes'], {})
        self.assertEqual(ProductVote.objects.count(), 0)

    def test_get_returns_votes(self):
        self.put({'candidateId': self.cand.pk, 'vote': 'up'})
        self.assertEqual(self.client.get(self.url).json()['votes'], {GID: 'up'})

    def test_strict_validation(self):
        for body in (
            {'candidateId': self.cand.pk, 'vote': 'up', 'extra': 1},
            {'candidateId': self.cand.pk, 'vote': 'meh'},
            {'candidateId': self.cand.pk, 'vote': 1},
            {'candidateId': self.cand.pk},
            {'candidateId': 'x', 'vote': 'up'},
            {'candidateId': 1.5, 'vote': 'up'},
            {'vote': 'up'},
            [],
        ):
            with self.subTest(body=body):
                self.assertEqual(self.put(body).status_code, 400)
        self.assertEqual(ProductVote.objects.count(), 0)

    def test_candidate_of_other_object_rejected(self):
        other = make_object('obj2', name='Chair')
        foreign = make_candidate(other, gid='gid://shopify/p/zzz')
        self.assertEqual(self.put({'candidateId': foreign.pk, 'vote': 'up'}).status_code, 400)

    def test_unknown_object_404(self):
        r = self.put({'candidateId': self.cand.pk, 'vote': 'up'}, url='/api/objects/nope/votes/')
        self.assertEqual(r.status_code, 404)

    def test_vote_survives_object_deletion(self):
        self.put({'candidateId': self.cand.pk, 'vote': 'up'})
        self.obj.delete()
        vote = ProductVote.objects.get()
        self.assertIsNone(vote.object_id)
        self.assertEqual(vote.product_gid, GID)

    def test_object_to_json_includes_votes(self):
        self.assertEqual(object_to_json(self.obj)['votes'], {})
        self.put({'candidateId': self.cand.pk, 'vote': 'down'})
        self.assertEqual(object_to_json(self.obj)['votes'], {GID: 'down'})

    def test_state_and_list_include_votes(self):
        make_object('obj3', name='Rug')
        self.put({'candidateId': self.cand.pk, 'vote': 'up'})
        by_id = {o['id']: o for o in self.client.get('/api/state/').json()['objects']}
        self.assertEqual(by_id['obj1']['votes'], {GID: 'up'})
        self.assertEqual(by_id['obj3']['votes'], {})
        listed = {o['id']: o for o in self.client.get('/api/objects/').json()}
        self.assertEqual(listed['obj1']['votes'], {GID: 'up'})
