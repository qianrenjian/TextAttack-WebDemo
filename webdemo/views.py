from django import forms
from django.shortcuts import render
from django.http import HttpResponseRedirect, HttpResponse, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.db.models.functions import Now
from captum.attr import IntegratedGradients, LayerConductance, LayerIntegratedGradients, LayerDeepLiftShap, InternalInfluence, LayerGradientXActivation
from captum.attr import configure_interpretable_embedding_layer
from captum.attr import visualization as viz
from copy import deepcopy
import torch
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt

import textattack
import transformers
import uuid
import json
import re
import os
import io
import sys
import urllib
import base64

from .config import MODELS, ATTACK_RECIPES, HIDDEN_ATTACK_RECIPES

MODEL_NAMES = [(x, x) for x in list(re.sub(r'-mr$', '-rotten_tomatoes', m) for m in MODELS.keys())]
RECIPE_NAME = [(x, x) for x in list(sorted(ATTACK_RECIPES.keys()))]

class Args():
    def __init__(self, model, recipe, model_batch_size=32, query_budget=200, model_cache_size=2**18, constraint_cache_size=2**18):
        self.model = model
        self.recipe = recipe
        self.model_batch_size = model_batch_size
        self.model_cache_size = model_cache_size
        self.query_budget = query_budget
        self.constraint_cache_size = constraint_cache_size

    def __getattr__(self, item):
        return False

class CustomData(forms.Form):
    input_text = forms.CharField(label='Custom Data', widget=forms.TextInput(attrs={'class' : 'form-control customDataInput'}))
    model_name = forms.CharField(label='Model Name', widget=forms.Select(choices=MODEL_NAMES, attrs={'class' : 'form-control'}))
    recipe_name = forms.CharField(label='Recipe Name', widget=forms.Select(choices=RECIPE_NAME, attrs={'class' : 'form-control'}))

def captum_form(encoded, device):
    input_dict = {k: [_dict[k] for _dict in encoded] for k in encoded[0]}
    batch_encoded = { k: torch.tensor(v).to(device) for k, v in input_dict.items()}
    return batch_encoded

def formatDisplay(datarecords):
    rows = []
    for datarecord in datarecords:
        rows.append(
            viz.format_word_importances(
                datarecord.raw_input, datarecord.word_attributions
            )
        )

    return rows

@csrf_exempt
def index(request):
    form = CustomData()
    STORED_POSTS = request.session.get("TextAttackResult")
    if not STORED_POSTS:
        STORED_POSTS = "[]"

    return render(request, 'webdemo/index.html', {'form': form, 'posts': json.loads(STORED_POSTS)})

@csrf_exempt
def attack_interactive(request):
    if request.method == 'POST':
        STORED_POSTS = request.session.get("TextAttackResult")
        form = CustomData(request.POST)
        if form.is_valid():
            if os.fork() == 0:
                input_text, model_name, recipe_name = form.cleaned_data['input_text'], form.cleaned_data['model_name'], form.cleaned_data['recipe_name']
                found = False
                if STORED_POSTS:
                    JSON_STORED_POSTS = json.loads(STORED_POSTS)
                    for idx, el in enumerate(JSON_STORED_POSTS):
                        if el["type"] == "attack" and el["input_string"] == input_text:
                            tmp = JSON_STORED_POSTS.pop(idx)
                            JSON_STORED_POSTS.insert(0, tmp)
                            found = True
                            break
                    
                    if found:
                        request.session["TextAttackResult"] = json.dumps(JSON_STORED_POSTS[:10])
                        return HttpResponseRedirect(reverse('webdemo:index'))

                attack = textattack.commands.attack.attack_args_helpers.parse_attack_from_args(Args(model_name, recipe_name))
                attacked_text = textattack.shared.attacked_text.AttackedText(input_text)
                attack.goal_function.init_attack_example(attacked_text, 1)
                goal_func_result, _ = attack.goal_function.get_result(attacked_text)
                
                input_label = goal_func_result.output
                raw_output = [float(x) for x in list(goal_func_result.raw_output)]
                input_histogram = json.dumps(raw_output)

                result = next(attack.attack_dataset([(input_text, goal_func_result.output)]))
                result_parsed = result.str_lines()
                if len(result_parsed) < 3:
                    return HttpResponseNotFound('Failed')
                output_text = result_parsed[2]

                attacked_text_out = textattack.shared.attacked_text.AttackedText(output_text)
                attack.goal_function.init_attack_example(attacked_text_out, 1)
                goal_func_result, _ = attack.goal_function.get_result(attacked_text_out)

                output_label = goal_func_result.output
                raw_output = [float(x) for x in list(goal_func_result.raw_output)]
                output_histogram = json.dumps(raw_output)

                post = {
                            "type": "attack",
                            "input_string": input_text, 
                            "model_name": model_name, 
                            "recipe_name": recipe_name, 
                            "output_string": output_text,
                            "input_histogram": input_histogram, 
                            "output_histogram": output_histogram, 
                            "input_label": input_label, 
                            "output_label": output_label
                        }
                
                if STORED_POSTS:
                    JSON_STORED_POSTS = json.loads(STORED_POSTS)
                    JSON_STORED_POSTS.insert(0, post)
                    request.session["TextAttackResult"] = json.dumps(JSON_STORED_POSTS[:10])
                else:
                    request.session["TextAttackResult"] = json.dumps([post])

                sys.exit(0)

        else:
            return HttpResponseNotFound('Failed')

        return HttpResponseRedirect(reverse('webdemo:index'))

    return HttpResponseNotFound('<h1>Not Found</h1>')

def captum_form(encoded, device):
    input_dict = {k: [_dict[k] for _dict in encoded] for k in encoded[0]}
    batch_encoded = { k: torch.tensor(v).to(device) for k, v in input_dict.items()}
    return batch_encoded

def calculate(clone, input_ids, token_type_ids, attention_mask):
    return clone.model(input_ids,token_type_ids,attention_mask)[0]

@csrf_exempt
def captum_interactive(request):
    if request.method == 'POST':
        STORED_POSTS = request.session.get("TextAttackResult")
        form = CustomData(request.POST)
        if form.is_valid():
            input_text, model_name, recipe_name = form.cleaned_data['input_text'], form.cleaned_data['model_name'], form.cleaned_data['recipe_name']
            found = False
            if STORED_POSTS:
                JSON_STORED_POSTS = json.loads(STORED_POSTS)
                for idx, el in enumerate(JSON_STORED_POSTS):
                    if el["type"] == "captum" and el["input_string"] == input_text:
                        tmp = JSON_STORED_POSTS.pop(idx)
                        JSON_STORED_POSTS.insert(0, tmp)
                        found = True
                        break
                
                if found:
                    request.session["TextAttackResult"] = json.dumps(JSON_STORED_POSTS[:10])
                    return HttpResponseRedirect(reverse('webdemo:index'))

            original_model = transformers.AutoModelForSequenceClassification.from_pretrained("textattack/" + model_name)
            original_tokenizer = textattack.models.tokenizers.AutoTokenizer("textattack/" + model_name)
            model = textattack.models.wrappers.HuggingFaceModelWrapper(original_model,original_tokenizer)

            device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")
            clone = deepcopy(model)
            clone.model.to(device)

            def calculate(input_ids, token_type_ids, attention_mask):
                return clone.model(input_ids,token_type_ids,attention_mask)[0]

            attack = textattack.commands.attack.attack_args_helpers.parse_attack_from_args(Args(model_name, recipe_name))
            attacked_text = textattack.shared.attacked_text.AttackedText(input_text)
            attack.goal_function.init_attack_example(attacked_text, 1)
            goal_func_result, _ = attack.goal_function.get_result(attacked_text)

            result = next(attack.attack_dataset([(input_text, goal_func_result.output)]))
            result_parsed = result.str_lines()
            if len(result_parsed) < 3:
                return HttpResponseNotFound('Failed')
            output_text = result_parsed[2]

            attacked_text_out = textattack.shared.attacked_text.AttackedText(output_text)

            orig = result.original_text()
            pert = result.perturbed_text()

            encoded = model.tokenizer.batch_encode([orig])
            batch_encoded = captum_form(encoded, device)
            x = calculate(**batch_encoded)

            pert_encoded = model.tokenizer.batch_encode([pert])
            pert_batch_encoded = captum_form(pert_encoded, device)
            x_pert = calculate(**pert_batch_encoded) 

            lig = LayerIntegratedGradients(calculate, clone.model.bert.embeddings)
            attributions,delta = lig.attribute(inputs=batch_encoded['input_ids'],
                                    additional_forward_args=(batch_encoded['token_type_ids'], batch_encoded['attention_mask']),
                                    n_steps = 10,
                                    target = torch.argmax(calculate(**batch_encoded)).item(),
                                    return_convergence_delta=True
                                )

            attributions_pert,delta_pert = lig.attribute(inputs=pert_batch_encoded['input_ids'],
                                    additional_forward_args=(pert_batch_encoded['token_type_ids'], pert_batch_encoded['attention_mask']),
                                    n_steps = 10,
                                    target = torch.argmax(calculate(**pert_batch_encoded)).item(),
                                    return_convergence_delta=True
                                )

            orig = original_tokenizer.tokenizer.tokenize(orig)
            pert = original_tokenizer.tokenizer.tokenize(pert)

            atts = attributions.sum(dim=-1).squeeze(0)
            atts = atts / torch.norm(atts)
                    
            atts_pert = attributions_pert.sum(dim=-1).squeeze(0)
            atts_pert = atts_pert / torch.norm(atts)                

            all_tokens = original_tokenizer.tokenizer.convert_ids_to_tokens(batch_encoded['input_ids'][0])
            all_tokens_pert = original_tokenizer.tokenizer.convert_ids_to_tokens(pert_batch_encoded['input_ids'][0])

            v = viz.VisualizationDataRecord(
                            atts[:45].detach().cpu(),
                            torch.max(x).item(),
                            torch.argmax(x,dim=1).item(),
                            goal_func_result.output,
                            2,
                            atts.sum().detach(), 
                            all_tokens[:45],
                            delta)

            v_pert = viz.VisualizationDataRecord(
                            atts_pert[:45].detach().cpu(),
                            torch.max(x_pert).item(),
                            torch.argmax(x_pert,dim=1).item(),
                            goal_func_result.output,
                            2,
                            atts_pert.sum().detach(), 
                            all_tokens_pert[:45],
                            delta_pert)

            formattedHTML = formatDisplay([v, v_pert])

            post = {
                        "type": "captum",
                        "input_string": input_text, 
                        "model_name": model_name, 
                        "recipe_name": recipe_name, 
                        "output_string": output_text,
                        "html_input_string": formattedHTML[0],
                        "html_output_string": formattedHTML[1],
                    }
            
            if STORED_POSTS:
                JSON_STORED_POSTS = json.loads(STORED_POSTS)
                JSON_STORED_POSTS.insert(0, post)
                request.session["TextAttackResult"] = json.dumps(JSON_STORED_POSTS[:10])
            else:
                request.session["TextAttackResult"] = json.dumps([post])

            return HttpResponseRedirect(reverse('webdemo:index'))

        else:
            return HttpResponseNotFound('Failed')

        return HttpResponse('Success')

    return HttpResponseNotFound('<h1>Not Found</h1>')

@csrf_exempt
def captum_heatmap_interactive(request):
    if request.method == 'POST':
        STORED_POSTS = request.session.get("TextAttackResult")
        form = CustomData(request.POST)
        if form.is_valid():
            input_text, model_name, recipe_name = form.cleaned_data['input_text'], form.cleaned_data['model_name'], form.cleaned_data['recipe_name']
            found = False
            if STORED_POSTS:
                JSON_STORED_POSTS = json.loads(STORED_POSTS)
                for idx, el in enumerate(JSON_STORED_POSTS):
                    if el["type"] == "heatmap" and el["input_string"] == input_text:
                        tmp = JSON_STORED_POSTS.pop(idx)
                        JSON_STORED_POSTS.insert(0, tmp)
                        found = True
                        break
                
                if found:
                    request.session["TextAttackResult"] = json.dumps(JSON_STORED_POSTS[:10])
                    return HttpResponseRedirect(reverse('webdemo:index'))

            original_model = transformers.AutoModelForSequenceClassification.from_pretrained("textattack/" + model_name)
            original_tokenizer = textattack.models.tokenizers.AutoTokenizer("textattack/" + model_name)
            model = textattack.models.wrappers.HuggingFaceModelWrapper(original_model,original_tokenizer)

            device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")
            clone = deepcopy(model)
            clone.model.to(device)

            def calculate(input_ids, token_type_ids, attention_mask):
                return clone.model(input_ids,token_type_ids,attention_mask)[0]

            interpretable_embedding = configure_interpretable_embedding_layer(clone.model, 'bert.embeddings')
            ref_token_id = original_tokenizer.tokenizer.pad_token_id
            sep_token_id = original_tokenizer.tokenizer.sep_token_id
            cls_token_id = original_tokenizer.tokenizer.cls_token_id

            def summarize_attributions(attributions):
                attributions = attributions.sum(dim=-1).squeeze(0)
                attributions = attributions / torch.norm(attributions)
                return attributions

            def construct_attention_mask(input_ids):
                return torch.ones_like(input_ids)

            def construct_input_ref_pos_id_pair(input_ids):
                seq_length = input_ids.size(1)
                position_ids = torch.arange(seq_length, dtype=torch.long, device=device)
                ref_position_ids = torch.zeros(seq_length, dtype=torch.long, device=device)

                position_ids = position_ids.unsqueeze(0).expand_as(input_ids)
                ref_position_ids = ref_position_ids.unsqueeze(0).expand_as(input_ids)
                return position_ids, ref_position_ids

            def squad_pos_forward_func(inputs, token_type_ids=None, attention_mask=None):
                pred = calculate(inputs, token_type_ids, attention_mask)
                return pred.max(1).values

            def construct_input_ref_token_type_pair(input_ids, sep_ind=0):
                seq_len = input_ids.size(1)
                token_type_ids = torch.tensor([[0 if i <= sep_ind else 1 for i in range(seq_len)]], device=device)
                ref_token_type_ids = torch.zeros_like(token_type_ids, device=device)# * -1
                return token_type_ids, ref_token_type_ids

            input_text_ids = original_tokenizer.tokenizer.encode(input_text, add_special_tokens=False)
            input_ids = [cls_token_id] + input_text_ids + [sep_token_id]
            input_ids = torch.tensor([input_ids], device=device)

            position_ids, ref_position_ids = construct_input_ref_pos_id_pair(input_ids)
            ref_input_ids = [cls_token_id] + [ref_token_id] * len(input_text_ids) + [sep_token_id]
            ref_input_ids = torch.tensor([ref_input_ids], device=device)

            token_type_ids, ref_token_type_ids = construct_input_ref_token_type_pair(input_ids, len(input_text_ids))
            attention_mask = torch.ones_like(input_ids)

            input_embeddings = interpretable_embedding.indices_to_embeddings(input_ids, token_type_ids=token_type_ids, position_ids=position_ids)
            ref_input_embeddings  = interpretable_embedding.indices_to_embeddings(ref_input_ids, token_type_ids=ref_token_type_ids, position_ids=ref_position_ids)

            layer_attrs_start = []

            for i in range(len(clone.model.bert.encoder.layer)):
                lc = LayerConductance(squad_pos_forward_func, clone.model.bert.encoder.layer[i])
                layer_attributions_start = lc.attribute(
                    inputs=input_embeddings,
                    baselines=ref_input_embeddings, 
                    additional_forward_args=(token_type_ids, attention_mask)
                )[0]

                layer_attrs_start.append(summarize_attributions(layer_attributions_start).cpu().detach().tolist())


            all_tokens = original_tokenizer.tokenizer.convert_ids_to_tokens(input_ids[0])

            fig, ax = plt.subplots(figsize=(15,5))
            xticklabels=all_tokens
            yticklabels=list(range(1,13))
            ax = sns.heatmap(np.array(layer_attrs_start), xticklabels=xticklabels, yticklabels=yticklabels, linewidth=0.2)
            plt.xlabel('Tokens')
            plt.ylabel('Layers')

            buf = io.BytesIO()
            fig.savefig(buf, format='png')
            buf.seek(0)
            bufferString = base64.b64encode(buf.read())
            imageUri = urllib.parse.quote(bufferString)

            post = {
                        "type": "heatmap",
                        "input_string": input_text, 
                        "model_name": model_name, 
                        "recipe_name": recipe_name, 
                        "image": imageUri,
                    }
            
            if STORED_POSTS:
                JSON_STORED_POSTS = json.loads(STORED_POSTS)
                JSON_STORED_POSTS.insert(0, post)
                request.session["TextAttackResult"] = json.dumps(JSON_STORED_POSTS[:10])
            else:
                request.session["TextAttackResult"] = json.dumps([post])

            return HttpResponseRedirect(reverse('webdemo:index'))

        else:
            return HttpResponseNotFound('Failed')

        return HttpResponse('Success')

    return HttpResponseNotFound('<h1>Not Found</h1>')