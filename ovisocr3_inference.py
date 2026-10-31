"""Dynamic one/two-image inference without changing the legacy parse wrapper."""
import re
from fastapi import HTTPException


def infer(parser, backend, images, prompt, *, max_tokens, min_pixels, max_pixels):
    if not 1 <= len(images) <= 2:
        raise HTTPException(422, 'An OvisOCR3 task accepts one or two images per request')
    if backend == 'vllm':
        content = [{'type': 'image'} for _ in images] + [{'type': 'text', 'text': prompt}]
        rendered = parser.model.get_tokenizer().apply_chat_template(
            [{'role': 'user', 'content': content}], tokenize=False, add_generation_prompt=True, enable_thinking=False)
        import copy
        sampling_params = copy.copy(parser.sampling_params)
        sampling_params.max_tokens = max_tokens
        outputs = parser.model.generate([{
            'prompt': rendered, 'multi_modal_data': {'image': images},
            'mm_processor_kwargs': {'images_kwargs': {'min_pixels': min_pixels, 'max_pixels': max_pixels}},
        }], sampling_params)
        if len(outputs) != 1 or not outputs[0].outputs:
            raise HTTPException(502, 'OvisOCR3 returned no task output')
        output = outputs[0].outputs[0]
        if output.finish_reason != 'stop':
            raise HTTPException(502, 'OvisOCR3 task output is incomplete: ' + str(output.finish_reason))
        return output.text.strip()
    if backend == 'transformers':
        import torch
        content = [{'type': 'image', 'image': image} for image in images] + [{'type': 'text', 'text': prompt}]
        inputs = parser.processor.apply_chat_template(
            [{'role': 'user', 'content': content}], tokenize=True, add_generation_prompt=True,
            enable_thinking=False, return_dict=True, return_tensors='pt',
            processor_kwargs={'images_kwargs': {'min_pixels': min_pixels, 'max_pixels': max_pixels}})
        inputs = {key: value.to(parser.device) if hasattr(value, 'to') else value for key, value in inputs.items()}
        length = inputs['input_ids'].shape[-1]
        with torch.inference_mode():
            output = parser.model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False, use_cache=True)
        if output.shape[-1] - length >= max_tokens:
            raise HTTPException(502, 'OvisOCR3 task generation reached the output limit')
        return re.sub(r'\n*<think>\s*</think>\n*', '\n\n', parser.processor.decode(output[0][length:], skip_special_tokens=True)).strip()
    if backend == 'mlx':
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        rendered = apply_chat_template(parser.processor, parser.model.config, prompt,
                                      num_images=len(images), enable_thinking=False, thinking_mode='disabled')
        output = generate(parser.model, parser.processor, rendered, image=images, max_tokens=max_tokens,
                          temperature=0.0, min_pixels=min_pixels, max_pixels=max_pixels,
                          enable_thinking=False, skip_special_tokens=True, verbose=False)
        if output.finish_reason not in {'stop', 'eos'}:
            raise HTTPException(502, 'OvisOCR3 task output is incomplete: ' + str(output.finish_reason))
        return re.sub(r'\n*<think>\s*</think>\n*', '\n\n', output.text).strip()
    raise HTTPException(422, 'Unsupported OvisOCR3 backend')
