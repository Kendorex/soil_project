"""Repair independent field choices to the SAME annual/horizon count bounds.

MILP chooses one existing legal sequence per field. It never changes transitions,
crop-count limits, management genes, or evaluates simulated objective values.
"""
import time


class CropMixRepair:
    def __init__(self,ctx,time_limit=5.):
        import numpy as np
        from scipy.optimize import Bounds,LinearConstraint
        from scipy.sparse import csc_matrix
        self.ctx=ctx;self.fields=ctx['fields'];self.cache={};self.time_limit=time_limit
        self.calls=self.changed=self.solves=0;self.seconds=0.;self.solver_status={}
        categories=ctx['planning']['farm_crop_mix']['categories'];years=ctx['planning']['years']
        self.mapping={s:c for c,spec in categories.items() for s in spec['states']}
        self.alternatives=[ctx['pool']['fields'][f]['alternatives'] for f in self.fields]
        self.columns=[(f,j) for f,alts in enumerate(self.alternatives) for j in range(len(alts))]
        rows=[];low=[];high=[]
        for field in range(len(self.fields)):
            rows.append([float(f==field) for f,j in self.columns]);low.append(1.);high.append(1.)
        for year in range(years):
            for category,spec in categories.items():
                rows.append([float(self.mapping[self.alternatives[f][j]['sequence_ids'][year]]==category) for f,j in self.columns])
                low.append(spec['annual_min']);high.append(spec['annual_max'])
        for category,spec in categories.items():
            rows.append([sum(self.mapping[s]==category for s in self.alternatives[f][j]['sequence_ids']) for f,j in self.columns])
            low.append(spec.get('horizon_min',0));high.append(spec.get('horizon_max',len(self.fields)*years))
        self.matrix=csc_matrix(np.array(rows));self.low=np.array(low);self.high=np.array(high)
        self.constraint=LinearConstraint(self.matrix,self.low,self.high)
        self.bounds=Bounds(np.zeros(len(self.columns)),np.ones(len(self.columns)))

    def valid(self,indices):
        import numpy as np
        x=np.array([float(indices[f]==j) for f,j in self.columns])
        values=self.matrix@x
        return bool(np.all(values>=self.low-1e-8) and np.all(values<=self.high+1e-8))

    def __call__(self,vector):
        import numpy as np
        from scipy.optimize import milp
        output=np.array(vector,dtype=int,copy=True);n=len(self.fields)
        if len(output)<n:raise ValueError('Too few map decision variables')
        indices=tuple(int(i) for i in output[:n])
        if any(not 0<=i<len(a) for i,a in zip(indices,self.alternatives)):
            raise ValueError('Sequence index outside domain')
        self.calls+=1
        if self.valid(indices):return output
        self.changed+=1
        if indices not in self.cache:
            started=time.monotonic()
            cost=np.array([float(j!=indices[f])+.01*sum(a!=b for a,b in zip(
                self.alternatives[f][j]['sequence_ids'],self.alternatives[f][indices[f]]['sequence_ids']))+j*1e-7
                for f,j in self.columns])
            solution=milp(cost,integrality=np.ones(len(cost)),bounds=self.bounds,constraints=self.constraint,
                          options={'time_limit':self.time_limit,'mip_rel_gap':0.})
            self.seconds+=time.monotonic()-started;self.solves+=1
            self.solver_status[str(solution.status)]=self.solver_status.get(str(solution.status),0)+1
            if solution.x is None:raise ValueError('No crop-count-compliant map found before simulation: '+solution.message)
            chosen=[None]*n
            for (f,j),v in zip(self.columns,solution.x):
                if v>.5:chosen[f]=j
            if any(v is None for v in chosen) or not self.valid(chosen):
                raise ValueError('MILP returned an invalid crop-map repair')
            self.cache[indices]=chosen
        output[:n]=self.cache[indices]
        return output

    def summary(self):
        return {'method':'MILP nearest legal field-sequence assignment; unchanged annual/horizon bounds',
                'calls':self.calls,'changed_calls':self.changed,'unique_solved_maps':self.solves,
                'solver_seconds':self.seconds,'solver_status_counts':self.solver_status,
                'management_genes_unchanged':True}
