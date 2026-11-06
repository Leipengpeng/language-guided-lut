"""Explicit statistical color-transfer baselines, not pretrained networks."""
import numpy as np
from color_match_lut import grid as identity_grid

RGB_XYZ=np.array([[.4124564,.3575761,.1804375],[.2126729,.7151522,.0721750],[.0193339,.1191920,.9503041]])
XYZ_RGB=np.linalg.inv(RGB_XYZ)
WHITE=np.array([.95047,1.,1.08883])


def to_lab(rgb):
    linear=np.where(rgb<=.04045,rgb/12.92,((np.maximum(rgb,0)+.055)/1.055)**2.4)
    xyz=(linear@RGB_XYZ.T)/WHITE
    delta=6/29
    f=np.where(xyz>delta**3,np.cbrt(xyz),xyz/(3*delta**2)+4/29)
    return np.stack([116*f[...,1]-16,500*(f[...,0]-f[...,1]),200*(f[...,1]-f[...,2])],axis=-1)


def from_lab(lab):
    y=(lab[...,0]+16)/116
    f=np.stack([y+lab[...,1]/500,y,y-lab[...,2]/200],axis=-1)
    delta=6/29
    xyz=np.where(f>delta,f**3,3*delta**2*(f-4/29))*WHITE
    linear=xyz@XYZ_RGB.T
    return np.where(linear<=.0031308,12.92*linear,1.055*np.maximum(linear,0)**(1/2.4)-.055)


def fit_lut(content_rgb,reference_rgb,size=65,method="reinhard_lab"):
    src=to_lab(content_rgb.reshape(-1,3)[::max(1,content_rgb.size//3//100000)])
    ref=to_lab(reference_rgb.reshape(-1,3)[::max(1,reference_rgb.size//3//100000)])
    metadata={"pretrained_model":False,"working_space":"CIE Lab D65","sample_limit":100000}
    if method=="reinhard_lab":
        mean_s,mean_r=src.mean(axis=0),ref.mean(axis=0)
        scale=ref.std(axis=0)/np.maximum(src.std(axis=0),1e-3)
        def lab_map(x):return (x-mean_s)*scale+mean_r
        metadata.update(algorithm="Reinhard-style mean and standard deviation color transfer in CIE Lab",
                        channel_scale=scale.tolist())
    elif method=="histogram_lab":
        quantiles=np.linspace(0,1,2049)
        s=np.quantile(src,quantiles,axis=0);r=np.quantile(ref,quantiles,axis=0)
        maps=[]
        for channel in range(3):
            unique,inds=np.unique(s[:,channel],return_index=True)
            maps.append((unique,r[inds,channel]))
        def lab_map(x):
            return np.stack([np.interp(x[...,j],maps[j][0],maps[j][1]) for j in range(3)],axis=-1)
        metadata.update(algorithm="Per-channel monotone quantile histogram matching in CIE Lab",quantile_steps=2049)
    else:raise ValueError(method)
    def mapping(rgb):return np.clip(from_lab(lab_map(to_lab(rgb))),0,1).astype(np.float32)
    return {"values":mapping(identity_grid(size)),"native_output":mapping(content_rgb),"metadata":metadata}
