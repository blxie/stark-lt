import sys
import os
filepath = os.path.abspath(__file__)
prj_dir = os.path.join(os.path.dirname(filepath), "../../..")
lib_dir = os.path.join(prj_dir, "lib")
sys.path.append(prj_dir)
sys.path.append(lib_dir)
from lib.test.vot21.stark_ref_vot21_rgbd import run_vot_exp
os.environ['CUDA_VISIBLE_DEVICES'] = '5'
run_vot_exp(base_tracker='stark_st', base_param='baseline_deit_long', ref_tracker="stark_ref", ref_param="baseline",
            use_new_box=False, vis=False)
