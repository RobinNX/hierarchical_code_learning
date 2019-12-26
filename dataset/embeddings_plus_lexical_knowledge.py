#!/usr/bin/env python
# -*- coding:utf-8 -*-

import io, os, copy
from typing import List, Tuple, Dict, Optional, Union
import random

import torch
from torch.utils.data import Dataset, DataLoader
import numpy as np

from .word_embeddings import AbstractWordEmbeddingsDataset
from .lexical_knowledge import HyponymyDataset, WordNetHyponymyDataset
from .utils import BasicTaxonomy, WordNetTaxonomy


class WordEmbeddingsAndHyponymyDataset(Dataset):

    def __init__(self, word_embeddings_dataset: AbstractWordEmbeddingsDataset, hyponymy_dataset: HyponymyDataset,
                 embedding_batch_size: int, hyponymy_batch_size: int, verbose: bool = False, **kwargs_hyponymy_dataloader):

        assert embedding_batch_size >= 2*hyponymy_batch_size, f"`embedding_batch_size` must be two times larger than `hyponymy_batch_size`."

        self._word_embeddings_dataset = word_embeddings_dataset
        self._hyponymy_dataset = hyponymy_dataset
        self._n_embeddings = len(word_embeddings_dataset)
        self._n_hyponymy = len(hyponymy_dataset)

        self._embedding_batch_size = embedding_batch_size
        self._hyponymy_batch_size = hyponymy_batch_size
        self._hyponymy_dataloader = DataLoader(hyponymy_dataset, collate_fn=lambda v:v, batch_size=hyponymy_batch_size, **kwargs_hyponymy_dataloader)
        self._verbose = verbose

        if verbose:
            self.verify_batch_sizes()

    def verify_batch_sizes(self):

        n_embeddings = len(self._word_embeddings_dataset)
        n_hyponymy = len(self._hyponymy_dataset)

        coef_effective_samples = 1 - 1/np.e
        n_iteration = n_hyponymy / self._hyponymy_batch_size
        n_embeddings_sample_in_batch = self._embedding_batch_size - 2*self._hyponymy_batch_size

        n_embeddings_used_in_epoch = coef_effective_samples * n_iteration * n_embeddings_sample_in_batch
        ratio = n_embeddings_used_in_epoch / n_embeddings

        balanced_embedding_batch_size = np.ceil((n_embeddings/(coef_effective_samples*n_iteration)) + 2*self._hyponymy_batch_size)
        balanced_hyponymy_batch_size = np.ceil(n_hyponymy*self._embedding_batch_size*coef_effective_samples/(n_embeddings+2*coef_effective_samples*n_hyponymy))

        print(f"hyponymy relations: {n_hyponymy}")
        print(f"embeddings: {n_embeddings}")
        print(f"embeddings referenced in epoch: {n_embeddings_used_in_epoch:.0f}")
        print(f"consumption ratio: {ratio:2.3f}")
        print(f"balanced `embedding_batch_size` value: {balanced_embedding_batch_size:.0f}")
        print(f"balanced `hyponymy_batch_size` value: {balanced_hyponymy_batch_size:.0f}")

    def is_encodable_all(self, *tokens):
        return all([self._word_embeddings_dataset.is_encodable(token) for token in tokens])

    def _create_batch_from_hyponymy_samples(self, batch_hyponymy: List[Dict[str,Union[str,float]]]):

        # take tokens from hyponymy pairs
        set_tokens = set()
        for hyponymy in batch_hyponymy:
            set_tokens.add(hyponymy["hyponym"])
            set_tokens.add(hyponymy["hypernym"])

        # take remaining tokens randomly from word embeddings dataset vocabulary
        n_diff = self._embedding_batch_size - len(set_tokens)
        if n_diff > 0:
            lst_index = np.random.choice(range(self._n_embeddings), size=n_diff, replace=False)
            lst_tokens_from_embeddings = [self._word_embeddings_dataset.index_to_entity(idx) for idx in lst_index]
            # add to token set
            set_tokens.update(lst_tokens_from_embeddings)
        else:
            lst_tokens_from_embeddings = []

        # create temporary token-to-index mapping
        lst_tokens = list(set_tokens)
        token_to_index = {token:idx for idx, token in enumerate(lst_tokens)}

        # create embeddings
        mat_embeddings = np.stack([self._word_embeddings_dataset[token]["embedding"] for token in lst_tokens])

        # create hyponymy relations
        lst_hyponymy_relation = []
        for hyponymy in batch_hyponymy:
            idx_hypo = token_to_index[hyponymy["hyponym"]]
            idx_hyper = token_to_index[hyponymy["hypernym"]]
            distance = hyponymy["distance"]
            lst_hyponymy_relation.append((idx_hyper, idx_hypo, distance))

        batch = {
            "embedding": mat_embeddings,
            "entity": lst_tokens,
            "hyponymy_relation": lst_hyponymy_relation,
            "hyponymy_relation_raw": batch_hyponymy
        }
        if self._verbose:
            batch["entity_sampled"] = lst_tokens_from_embeddings

        return batch

    def __iter__(self):

        for batch_hyponymy_b in self._hyponymy_dataloader:
            # remove hyponymy pairs which is not encodable
            batch_hyponymy = [sample for sample in batch_hyponymy_b if self.is_encodable_all(sample["hyponym"], sample["hypernym"])]
            if len(batch_hyponymy) == 0:
                continue

            batch = self._create_batch_from_hyponymy_samples(batch_hyponymy=batch_hyponymy)

            yield batch

    def __getitem__(self, idx):

        while True:
            n_idx_min = self._hyponymy_batch_size * idx
            n_idx_max = self._hyponymy_batch_size * (idx+1)

            batch_hyponymy_b = self._hyponymy_dataset[n_idx_min:n_idx_max]
            # remove hyponymy pairs which is not encodable
            batch_hyponymy = [sample for sample in batch_hyponymy_b if self.is_encodable_all(sample["hyponym"], sample["hypernym"])]
            if len(batch_hyponymy) == 0:
                idx += 1
                continue

            batch = self._create_batch_from_hyponymy_samples(batch_hyponymy=batch_hyponymy)

            return batch

    def n_samples(self):
        return self._embedding_batch_size * int(np.ceil(len(self._hyponymy_dataset) / self._hyponymy_batch_size))

    def __len__(self):
        return int(np.ceil(len(self._hyponymy_dataset) / self._hyponymy_batch_size))


class WordEmbeddingsAndHyponymyDatasetWithNonHyponymyRelation(WordEmbeddingsAndHyponymyDataset):

    def __init__(self, word_embeddings_dataset: AbstractWordEmbeddingsDataset, hyponymy_dataset: HyponymyDataset,
                 embedding_batch_size: int, hyponymy_batch_size: int, non_hyponymy_batch_size: Optional[int] = None,
                 non_hyponymy_relation_distance: Optional[float] = None,
                 non_hyponymy_relation_target: str = "hyponym",
                 exclude_reverse_hyponymy_from_non_hyponymy_relation: bool = True,
                 limit_hyponym_candidates_within_minibatch: bool = True,
                 split_hyponymy_and_non_hyponymy: bool = True,
                 verbose: bool = False, **kwargs_hyponymy_dataloader):

        super().__init__(word_embeddings_dataset, hyponymy_dataset,
                 embedding_batch_size, hyponymy_batch_size, verbose=False, **kwargs_hyponymy_dataloader)

        assert non_hyponymy_batch_size % hyponymy_batch_size == 0, f"`non_hyponymy_batch_size` must be a multiple of `hyponymy_batch_size`."
        if not limit_hyponym_candidates_within_minibatch:
            assert embedding_batch_size >= 2*hyponymy_batch_size + non_hyponymy_batch_size, \
            f"`embedding_batch_size` must be larger than `2*hyponymy_batch_size + non_hyponymy_batch_size`."

        available_options = "hyponym,hypernym,both"
        assert non_hyponymy_relation_target in available_options.split(","), f"`non_hyponymy_relation_target` must be one of these: {available_options}"

        self._non_hyponymy_batch_size = hyponymy_batch_size if non_hyponymy_batch_size is None else non_hyponymy_batch_size
        self._non_hyponymy_multiple = non_hyponymy_batch_size // hyponymy_batch_size
        self._non_hyponymy_relation_distance = non_hyponymy_relation_distance
        self._non_hyponymy_relation_target = non_hyponymy_relation_target
        self._exclude_reverse_hyponymy_from_non_hyponymy_relation = exclude_reverse_hyponymy_from_non_hyponymy_relation
        self._limit_hyponym_candidates_within_minibatch = limit_hyponym_candidates_within_minibatch
        self._split_hyponymy_and_non_hyponymy = split_hyponymy_and_non_hyponymy
        self._verbose = verbose

        if non_hyponymy_relation_target == "both":
            assert self._non_hyponymy_multiple % 2 == 0, f"if `non_hyponymy_relation_target=both`, `non_hyponymy_batch_size` must be an even multiple of the `hyponymy_batch_size`"

        if verbose:
            self.verify_batch_sizes()

        # build the taxonomy from hyponymy relation. it only uses direct hyponymy pairs.
        if isinstance(hyponymy_dataset, WordNetHyponymyDataset):
            self._taxonomy = WordNetTaxonomy(hyponymy_dataset=hyponymy_dataset)
        elif isinstance(hyponymy_dataset, HyponymyDataset):
            self._taxonomy = BasicTaxonomy(hyponymy_dataset=hyponymy_dataset)
        else:
            raise NotImplementedError(f"unsupported hyponymy dataset type: {type(hyponymy_dataset)}")

    def verify_batch_sizes(self):

        n_embeddings = len(self._word_embeddings_dataset)
        n_hyponymy = len(self._hyponymy_dataset)

        coef_effective_samples = 1 - 1/np.e
        n_iteration = n_hyponymy / self._hyponymy_batch_size
        if self._limit_hyponym_candidates_within_minibatch:
            n_embeddings_sample_in_batch = self._embedding_batch_size - 2*self._hyponymy_batch_size
            multiplier = 2
        else:
            n_embeddings_sample_in_batch = self._embedding_batch_size - 2*self._hyponymy_batch_size - self._non_hyponymy_batch_size
            multiplier = 2 + self._non_hyponymy_multiple

        n_embeddings_used_in_epoch = coef_effective_samples * n_iteration * n_embeddings_sample_in_batch
        ratio = n_embeddings_used_in_epoch / n_embeddings

        balanced_embeddings_sample_in_batch = (n_embeddings/coef_effective_samples) / n_iteration
        balanced_embedding_batch_size = np.ceil(self._embedding_batch_size - (n_embeddings_sample_in_batch - balanced_embeddings_sample_in_batch))
        balanced_hyponymy_batch_size = np.floor(n_hyponymy*self._embedding_batch_size*coef_effective_samples/(n_embeddings+multiplier*coef_effective_samples*n_hyponymy))

        print(f"hyponymy relations: {n_hyponymy}")
        print(f"non-hyponymy relations: {n_hyponymy*self._non_hyponymy_multiple}")
        print(f"embeddings: {n_embeddings}")
        print(f"embeddings referenced in epoch: {n_embeddings_used_in_epoch:.0f}")
        print(f"consumption ratio: {ratio:2.3f}")
        print(f"balanced `embedding_batch_size` value: {balanced_embedding_batch_size:.0f}")
        print(f"balanced `hyponymy_batch_size` value: {balanced_hyponymy_batch_size:.0f}")
        print(f"balanced `non_hyponymy_batch_size value: {balanced_hyponymy_batch_size*self._non_hyponymy_multiple:.0f}")

    def _create_non_hyponymy_samples_from_hyponymy_samples(self, batch_hyponymy: List[Dict[str,Union[str,float]]],
                                                            size_per_sample: int) -> List[Dict[str,Union[str,float]]]:

        # (optional) limit hyponym candidates within the entity that appears in the minibatch.
        if self._limit_hyponym_candidates_within_minibatch:
            set_candidates = set()
            for hyponymy in batch_hyponymy:
                set_candidates.add(hyponymy["hyponym"])
                set_candidates.add(hyponymy["hypernym"])
        else:
            set_candidates = None

        # create non-hyponymy relation
        lst_non_hyponymy_samples = []
        for hyponymy in batch_hyponymy:
            hyper = hyponymy["hypernym"]
            hypo = hyponymy["hyponym"]
            lst_tup_sample_b = []
            if isinstance(self._taxonomy, BasicTaxonomy):
                if self._non_hyponymy_relation_target in ("hyponym","both"):
                    lst_tup_sample_b_swap_hypo = self._taxonomy.sample_random_hyponyms(entity=hyper, candidates=set_candidates, size=size_per_sample,
                                                                exclude_hypernyms=self._exclude_reverse_hyponymy_from_non_hyponymy_relation)
                    lst_tup_sample_b.extend(lst_tup_sample_b_swap_hypo)
                if self._non_hyponymy_relation_target in ("hypernym","both"):
                    lst_tup_sample_b_swap_hyper = self._taxonomy.sample_random_hypernyms(entity=hypo, candidates=set_candidates, size=size_per_sample,
                                                                exclude_hypernyms=self._exclude_reverse_hyponymy_from_non_hyponymy_relation)
                    lst_tup_sample_b.extend(lst_tup_sample_b_swap_hyper)

            elif isinstance(self._taxonomy, WordNetTaxonomy):
                # ToDo: implement wordnet-specific taxonomy class
                raise NotImplementedError("not yet implemented.")

            for hypernym, hyponym, distance in lst_tup_sample_b:
                d = {
                    "hyponym":hyponym,
                    "hypernym":hypernym,
                    # if user explicitly specify the non-hyponymy relation distance, then update samples with its value.
                    "distance":distance if self._non_hyponymy_relation_distance is None else self._non_hyponymy_relation_distance
                }
                lst_non_hyponymy_samples.append(d)
            if self._verbose:
                if len(lst_tup_sample_b):
                    f"failed to sample hyponyms: {hyper}"

        return lst_non_hyponymy_samples

    def _split_hyponymy_samples_and_non_hyponymy_samples(self, batch):
        lst_hyponymy_relation = []
        lst_hyponymy_relation_raw = []
        lst_non_hyponymy_relation = []
        lst_non_hyponymy_relation_raw = []
        for relation, relation_raw  in zip(batch["hyponymy_relation"], batch["hyponymy_relation_raw"]):
            distance = relation[2]
            if distance > 0:
                lst_hyponymy_relation.append(relation)
                lst_hyponymy_relation_raw.append(relation_raw)
            else:
                lst_non_hyponymy_relation.append(relation)
                lst_non_hyponymy_relation_raw.append(relation_raw)

        batch["hyponymy_relation"] = lst_hyponymy_relation
        batch["hyponymy_relation_raw"] = lst_hyponymy_relation_raw
        batch["non_hyponymy_relation"] = lst_non_hyponymy_relation
        batch["non_hyponymy_relation_raw"] = lst_non_hyponymy_relation_raw

        return batch

    def __getitem__(self, idx):

        while True:
            n_idx_min = self._hyponymy_batch_size * idx
            n_idx_max = self._hyponymy_batch_size * (idx+1)

            # feed hyponymy relation from the hyponymy dataset
            batch_hyponymy_b = self._hyponymy_dataset[n_idx_min:n_idx_max]
            # remove hyponymy pairs which is not encodable
            batch_hyponymy = [sample for sample in batch_hyponymy_b if self.is_encodable_all(sample["hyponym"], sample["hypernym"])]
            if len(batch_hyponymy) == 0:
                idx += 1
                continue

            # we randomly sample the non-hyponymy relation from the mini-batch
            if self._non_hyponymy_relation_target == "both":
                size_per_sample = self._non_hyponymy_multiple // 2
            else:
                size_per_sample = self._non_hyponymy_multiple
            batch_non_hyponymy = self._create_non_hyponymy_samples_from_hyponymy_samples(batch_hyponymy=batch_hyponymy,
                                                                                         size_per_sample=size_per_sample)
            # create a minibatch from both hyponymy samples and non-hyponymy samples
            batch_hyponymy.extend(batch_non_hyponymy)
            batch = self._create_batch_from_hyponymy_samples(batch_hyponymy=batch_hyponymy)

            # (optional) split hyponymy relation and non-hyponymy relation
            if self._split_hyponymy_and_non_hyponymy:
                batch = self._split_hyponymy_samples_and_non_hyponymy_samples(batch)

            return batch

    def __iter__(self):

        for batch_hyponymy_b in self._hyponymy_dataloader:
            # remove hyponymy pairs which is not encodable
            batch_hyponymy = [sample for sample in batch_hyponymy_b if self.is_encodable_all(sample["hyponym"], sample["hypernym"])]
            if len(batch_hyponymy) == 0:
                continue

            # we randomly sample the non-hyponymy relation from the mini-batch
            if self._non_hyponymy_relation_target == "both":
                size_per_sample = self._non_hyponymy_multiple // 2
            else:
                size_per_sample = self._non_hyponymy_multiple
            batch_non_hyponymy = self._create_non_hyponymy_samples_from_hyponymy_samples(batch_hyponymy=batch_hyponymy,
                                                                                         size_per_sample=size_per_sample)
            # create a minibatch from both hyponymy samples and non-hyponymy samples
            batch_hyponymy.extend(batch_non_hyponymy)
            batch = self._create_batch_from_hyponymy_samples(batch_hyponymy=batch_hyponymy)

            # (optional) split hyponymy relation and non-hyponymy relation
            if self._split_hyponymy_and_non_hyponymy:
                batch = self._split_hyponymy_samples_and_non_hyponymy_samples(batch)

            yield batch
