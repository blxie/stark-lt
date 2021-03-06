from collections import deque

import numpy as np
import torch
import torch.nn.functional as F
# from models import *
from .matching import *
from .kalman_filter import KalmanFilter
from .log import logger

from .basetrack import BaseTrack, TrackState

# matching | basetracker | kalman_filter | log is for temporal cues

class STrack(BaseTrack):
    shared_kalman = KalmanFilter()
    def __init__(self, tlwh, score, temp_feat, buffer_size=30):

        # wait activate
        self._tlwh = np.asarray(tlwh, dtype=np.float)
        self.kalman_filter = None
        self.mean, self.covariance = None, None
        self.is_activated = False

        self.score = score
        self.tracklet_len = 0

        self.smooth_feat = None
        self.update_features(temp_feat)
        self.features = deque([], maxlen=buffer_size)
        self.alpha = 0.9

    def update_features(self, feat):
        feat /= np.linalg.norm(feat)
        self.curr_feat = feat
        if self.smooth_feat is None:
            self.smooth_feat = feat
        else:
            self.smooth_feat = self.alpha * self.smooth_feat + (1 - self.alpha) * feat
        self.features.append(feat)
        self.smooth_feat /= np.linalg.norm(self.smooth_feat)

    def predict(self):
        mean_state = self.mean.copy()
        if self.state != TrackState.Tracked:
            mean_state[7] = 0
        self.mean, self.covariance = self.kalman_filter.predict(mean_state, self.covariance)

    @staticmethod
    def multi_predict(stracks):
        if len(stracks) > 0:
            multi_mean = np.asarray([st.mean.copy() for st in stracks])
            multi_covariance = np.asarray([st.covariance for st in stracks])
            for i, st in enumerate(stracks):
                if st.state != TrackState.Tracked:
                    multi_mean[i][7] = 0
            multi_mean, multi_covariance = STrack.shared_kalman.multi_predict(multi_mean, multi_covariance)
            for i, (mean, cov) in enumerate(zip(multi_mean, multi_covariance)):
                stracks[i].mean = mean
                stracks[i].covariance = cov

    def activate(self, kalman_filter, frame_id):
        """Start a new tracklet"""
        self.kalman_filter = kalman_filter
        self.track_id = self.next_id()
        self.mean, self.covariance = self.kalman_filter.initiate(self.tlwh_to_xyah(self._tlwh))

        self.tracklet_len = 0
        self.state = TrackState.Tracked
        # if frame_id == 1:
        #     self.is_activated = True
        self.is_activated = True  #!!!!!!!!!!
        self.frame_id = frame_id
        self.start_frame = frame_id

    def re_activate(self, new_track, frame_id, new_id=False):
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_track.tlwh)
        )

        self.update_features(new_track.curr_feat)
        self.tracklet_len = 0
        self.state = TrackState.Tracked
        self.is_activated = True
        self.frame_id = frame_id
        if new_id:
            self.track_id = self.next_id()

    def update(self, new_track, frame_id, update_feature=True):
        """
        Update a matched track
        :type new_track: STrack
        :type frame_id: int
        :type update_feature: bool
        :return:
        """
        self.frame_id = frame_id
        self.tracklet_len += 1

        new_tlwh = new_track.tlwh
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_tlwh))
        self.state = TrackState.Tracked
        self.is_activated = True

        self.score = new_track.score
        if update_feature:
            self.update_features(new_track.curr_feat)

    @property
    # @jit(nopython=True)
    def tlwh(self):
        """Get current position in bounding box format `(top left x, top left y,
                width, height)`.
        """
        if self.mean is None:
            return self._tlwh.copy()
        ret = self.mean[:4].copy()
        ret[2] *= ret[3]
        ret[:2] -= ret[2:] / 2
        return ret

    @property
    # @jit(nopython=True)
    def tlbr(self):
        """Convert bounding box to format `(min x, min y, max x, max y)`, i.e.,
        `(top left, bottom right)`.
        """
        ret = self.tlwh.copy()
        ret[2:] += ret[:2]
        return ret

    @staticmethod
    # @jit(nopython=True)
    def tlwh_to_xyah(tlwh):
        """Convert bounding box to format `(center x, center y, aspect ratio,
        height)`, where the aspect ratio is `width / height`.
        """
        ret = np.asarray(tlwh).copy()
        ret[:2] += ret[2:] / 2
        ret[2] /= ret[3]
        return ret

    def to_xyah(self):
        return self.tlwh_to_xyah(self.tlwh)

    @staticmethod
    # @jit(nopython=True)
    def tlbr_to_tlwh(tlbr):
        ret = np.asarray(tlbr).copy()
        ret[2:] -= ret[:2]
        return ret

    @staticmethod
    # @jit(nopython=True)
    def tlwh_to_tlbr(tlwh):
        ret = np.asarray(tlwh).copy()
        ret[2:] += ret[:2]
        return ret

    def __repr__(self):
        return 'OT_{}_({}-{})'.format(self.track_id, self.start_frame, self.end_frame)


class AsscoiateTracker(object):
    def __init__(self, frame_rate=30):
        # if opt.gpus[0] >= 0:
        #     opt.device = torch.device('cuda')
        # else:
        #     opt.device = torch.device('cpu')
        # print('Creating model...')
        # self.model = create_model(opt.arch, opt.heads, opt.head_conv)
        # self.model = load_model(self.model, opt.load_model)
        # self.model = self.model.to(opt.device)
        # self.model.eval()

        self.tracked_stracks = []  # type: list[STrack]
        self.lost_stracks = []  # type: list[STrack]
        self.removed_stracks = []  # type: list[STrack]

        self.frame_id = 0
        self.det_thresh = 0
        self.track_buffer = 30
        self.buffer_size = int(frame_rate / 30.0 * self.track_buffer)
        self.max_time_lost = 5 # self.buffer_size  # ?????????????????????????????????????????????trackerlet
        # self.max_per_image = 100
        # self.mean = np.array([0.408, 0.447, 0.47], dtype=np.float32).reshape(1, 1, 3)
        # self.std = np.array([0.289, 0.274, 0.278], dtype=np.float32).reshape(1, 1, 3)

        self.kalman_filter = KalmanFilter()

    def initialize(self, current_dets,current_id_feature=None,add=False):
        if add:
            pass
        else:
            self.frame_id += 1
        dets = current_dets  # (n 5)
        activated_starcks = []
        u_detection = []
        for k in range(dets.shape[0]):
            u_detection.append(k)
        u_detection = np.array(u_detection)
        if len(dets) > 0:
            '''Detections'''
            detections = [STrack(STrack.tlbr_to_tlwh(tlbrs[:4]), tlbrs[4],30) for
                          tlbrs in dets[:, :5]]  # k???det,??????k???STrack
        else:
            detections = []
        # initialize tracklets
        for inew in u_detection:
            track = detections[inew]
            if track.score < self.det_thresh:
                continue
            track.activate(self.kalman_filter, self.frame_id)  # ??????track???????????????activated=T????????????False
            activated_starcks.append(track)

        self.tracked_stracks = [t for t in self.tracked_stracks if t.state == TrackState.Tracked]
        self.tracked_stracks = joint_stracks(self.tracked_stracks, activated_starcks)

    def update(self,current_dets,current_id_feature=None):

        self.frame_id += 1
        activated_starcks = []
        lost_stracks = []
        removed_stracks = []
        refind_stracks = []
        # refind_stracks = []

        dets = current_dets  # (n 5)
        if len(dets) > 0:
            '''Detections'''
            detections = [STrack(STrack.tlbr_to_tlwh(tlbrs[:4]), tlbrs[4],30) for
                          tlbrs in dets[:, :5]]  # k???det,??????k???STrack
        else:
            detections = []

        ''' Add newly detected tracklets to tracked_stracks'''
        unconfirmed = []
        tracked_stracks = []  # type: list[STrack]
        for track in self.tracked_stracks:
            if not track.is_activated:
                unconfirmed.append(track)
            else:
                tracked_stracks.append(track)
        #########################################################
        strack_pool = joint_stracks(tracked_stracks, self.lost_stracks)
        STrack.multi_predict(strack_pool)
        # dists = embedding_distance(strack_pool, detections)  # ?????????????????????????????????tracked_tracker?????????cosine??????
        # STrack.multi_predict(strack_pool)  # ???????????????
        # dists = fuse_motion(self.kalman_filter, dists, strack_pool,
        #                              detections)  # ?????????????????????detection???pool_stacker?????????????????????
        # matches, u_track, u_detection = linear_assignment(dists, thresh=0.7)  # ??????????????? // ???????????????????????????????????? // u_track???????????????tracker????????????
        #
        # for itracked, idet in matches:  # matches:63*2 , 63:detections????????????2???????????????tracked_tracker?????????????????????detection?????????
        #     track = strack_pool[itracked]
        #     det = detections[idet]
        #     if track.state == TrackState.Tracked:
        #         track.update(det, self.frame_id)  # ?????????pool_tracker???detection?????????????????????????????????
        #         activated_starcks.append(track)
        #########################################################
        u_detection = []  # tidaishangmiande
        for k in range(dets.shape[0]):
            u_detection.append(k)
        u_detection = np.array(u_detection)
        u_track = []
        for k in range(len(strack_pool)):
            u_track.append(k)
        u_track = np.array(u_track)
        #########################################################
        ''' detections, r_tracked_stracks??????IOU?????? '''
        detections = [detections[i] for i in u_detection]  # u_detection???????????????detection?????????
        # r_tracked_stracks = [strack_pool[i] for i in u_track if strack_pool[i].state == TrackState.Tracked]
        r_tracked_stracks = [strack_pool[i] for i in u_track]
        dists = iou_distance(r_tracked_stracks, detections,type='iou')
        matches, u_track, u_detection = linear_assignment(dists, thresh=0.7) # 0.5
        if len(matches) != 0:
            det_id = matches[0,1]
        else:
            det_id = []

        for itracked, idet in matches:
            track = r_tracked_stracks[itracked]
            det = detections[idet]
            if track.state == TrackState.Tracked:
                track.update(det, self.frame_id)
                activated_starcks.append(track)
            else:
                track.re_activate(det, self.frame_id, new_id=False)  # ????????????lost????????????????????????
                refind_stracks.append(track)

        for it in u_track:
            track = r_tracked_stracks[it]
            if not track.state == TrackState.Lost:
                track.mark_lost()
                lost_stracks.append(track)  # ??????tracked_tracker iou????????????tracker???????????????lost

        ''' ??????????????????detection???unconfirmed_stracks??????IOU?????? '''
        # detections = [detections[i] for i in u_detection]  # ???cosine/iou????????????detection???unconfirmed_tracker????????????
        # dists = iou_distance(unconfirmed, detections)
        # matches, u_unconfirmed, u_detection = linear_assignment(dists, thresh=0.7)
        # for itracked, idet in matches:
        #     unconfirmed[itracked].update(detections[idet], self.frame_id)
        #     activated_starcks.append(unconfirmed[itracked])
        # for it in u_unconfirmed:
        #     track = unconfirmed[it]
        #     track.mark_removed()
        #     removed_stracks.append(track)

        ''' ??????????????????detections???????????????unconfirmed_stracks??????tracker '''
        # for inew in u_detection:  # ???cosine/iou/uncofirmed_tracker???????????????detection?????????????????????unconfimed_tracker
        #     track = detections[inew]
        #     if track.score < self.det_thresh:
        #         continue
        #     track.activate(self.kalman_filter, self.frame_id)  # ??????track???????????????activated=T????????????False
        #     activated_starcks.append(track)

        """ Step 5: Update state"""
        for track in self.lost_stracks:
            if self.frame_id - track.end_frame > self.max_time_lost:
                track.mark_removed()
                removed_stracks.append(track)

        self.tracked_stracks = [t for t in self.tracked_stracks if t.state == TrackState.Tracked]
        self.tracked_stracks = joint_stracks(self.tracked_stracks, activated_starcks)
        self.lost_stracks = sub_stracks(self.lost_stracks, self.tracked_stracks)  # a-b
        self.lost_stracks.extend(lost_stracks)
        self.lost_stracks = sub_stracks(self.lost_stracks, self.removed_stracks)
        self.removed_stracks.extend(removed_stracks)
        self.tracked_stracks, self.lost_stracks = remove_duplicate_stracks(self.tracked_stracks, self.lost_stracks)
        # get scores of lost tracks
        output_stracks = [track for track in self.tracked_stracks if track.is_activated]

        # logger.debug('===========Local Frame {}=========='.format(self.frame_id))
        # logger.debug('Activated: {}'.format([track.track_id for track in activated_starcks]))
        # logger.debug('Refind: {}'.format([track.track_id for track in refind_stracks]))
        # logger.debug('Lost: {}'.format([track.track_id for track in lost_stracks]))
        # logger.debug('Removed: {}'.format([track.track_id for track in removed_stracks]))

        return output_stracks, u_detection,det_id


def joint_stracks(tlista, tlistb):  #
    exists = {}
    res = []
    for t in tlista:
        exists[t.track_id] = 1
        res.append(t)
    for t in tlistb:
        tid = t.track_id
        if not exists.get(tid, 0):
            exists[tid] = 1
            res.append(t)
    return res


def sub_stracks(tlista, tlistb):
    stracks = {}
    for t in tlista:
        stracks[t.track_id] = t
    for t in tlistb:
        tid = t.track_id
        if stracks.get(tid, 0):
            del stracks[tid]
    return list(stracks.values())


def remove_duplicate_stracks(stracksa, stracksb):
    pdist = iou_distance(stracksa, stracksb)
    pairs = np.where(pdist < 0.15)
    dupa, dupb = list(), list()
    for p, q in zip(*pairs):
        timep = stracksa[p].frame_id - stracksa[p].start_frame
        timeq = stracksb[q].frame_id - stracksb[q].start_frame
        if timep > timeq:
            dupb.append(q)
        else:
            dupa.append(p)
    resa = [t for i, t in enumerate(stracksa) if not i in dupa]
    resb = [t for i, t in enumerate(stracksb) if not i in dupb]
    return resa, resb

